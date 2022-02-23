"Miscellaneous estimation and data-massaging functions."
from collections import namedtuple
from concurrent.futures import ProcessPoolExecutor
import itertools
import json
import numpy as np
import pandas as pd
import scipy.interpolate
import scipy.optimize
import sys

from . import util, model, defaults
from .contig import Contig
from ._estimation_tools import (
    realign,
    thin_data,
    bin_observations,
    windowed_mutation_counts,
    beta_de_avg_pdf,
)


import logging
logger = logging.getLogger(__name__)


# Construct time intervals stuff
def extract_pieces(piece_str):
    """Convert PSMC-style piece string to model representation."""
    pieces = []
    for piece in piece_str.split("+"):
        try:
            num, span = list(map(int, piece.split("*")))
        except ValueError:
            span = int(piece)
            num = 1
        pieces += [span] * num
    return pieces


def construct_time_points(t1, tK, pieces, offset):
    s = np.diff(np.logspace(np.log10(offset + t1), np.log10(tK), sum(pieces) + 1))
    time_points = np.zeros(len(pieces))
    count = 0
    for i, p in enumerate(pieces):
        time_points[i] = s[count : (count + p)].sum()
        count += p
    return np.concatenate([[t1], time_points])


def compress_repeated_obs(dataset):
    # pad with illegal value at starting position
    nonce = np.zeros_like(dataset[0])
    nonce[:2] = [1, -999]
    dataset = np.r_[[nonce], dataset, [nonce]]
    nonreps = np.any(dataset[1:, 1:] != dataset[:-1, 1:], axis=1)
    newob = dataset[1:][nonreps]
    csw = np.cumsum(dataset[:, 0])[np.where(nonreps)]
    newob[:-1, 0] = csw[1:] - csw[:-1]
    return newob[:-1]


def decompress_polymorphic_spans(dataset):
    miss = np.all(dataset[:, 1::3] == -1, axis=1) & np.all(
        dataset[:, 3::3] == 0, axis=1
    )
    nonseg = np.all(dataset[:, 1::3] == 0, axis=1) & (
        np.all(dataset[:, 2::3] == dataset[:, 3::3], axis=1)
        | np.all(dataset[:, 2::3] == 0, axis=1)
    )
    psp = np.where((dataset[:, 0] > 1) & (~nonseg) & (~miss))[0]
    if not psp.size:
        return dataset
    last = 0
    first = True
    for i in psp:
        row = dataset[i]
        if first:
            ret = np.r_[dataset[last:i], np.tile(np.r_[1, row[1:]], (row[0], 1))]
            first = False
        else:
            ret = np.r_[ret, dataset[last:i], np.tile(np.r_[1, row[1:]], (row[0], 1))]
        last = i + 1
    ret = np.r_[ret, dataset[last:]]
    return ret


def recode_nonseg(contig, cutoff):
    warn_only = False
    if cutoff is None:
        cutoff = 50000
        warn_only = True
    d = contig.data
    runs = (
        (d[:, 0] > cutoff)
        & np.all(d[:, 1::3] == 0, axis=1)
        & np.all(d[:, 2::3] == 0, axis=1)
    )
    if np.any(runs):
        if warn_only:
            f = logger.warning
            txt = ""
        else:
            f = logger.debug
            txt = " (converted to missing)"
            d[runs, 1::3] = -1
            d[runs, 3::3] = 0
        f(
            "Long runs of homozygosity%s in contig %s: \n%s (base pairs)",
            txt,
            contig.fn,
            d[runs, 0],
        )
    return contig


def break_long_spans(contig, span_cutoff):
    # Spans longer than this are broken up
    contig_list = []
    obs_attributes = []
    obs = contig.data
    miss = np.zeros_like(obs[0])
    miss[0] = 1
    miss[1::3] = -1
    long_spans = np.where(
        (obs[:, 0] >= span_cutoff)
        & np.all(obs[:, 1::3] == -1, axis=1)
        & np.all(obs[:, 3::3] == 0, axis=1)
    )[0]
    cob = 0
    if obs[long_spans].size:
        logger.debug("Long missing spans:\n%s (base pairs)", (obs[long_spans, 0]))
    positions = np.insert(np.cumsum(obs[:, 0]), 0, 0)
    for x in long_spans.tolist() + [None]:
        s = obs[cob:x, 0].sum()
        contig_list.append(
            Contig(
                data=np.insert(obs[cob:x], 0, miss, 0),
                pid=contig.pid,
                fn=contig.fn,
                n=contig.n,
                a=contig.a,
            )
        )
        if contig.a[0] == 1:
            a_cols = [1, 4]
        else:
            assert contig.a[0] == 2
            a_cols = [1]
        last_data = contig_list[-1].data
        l = last_data[:, 0].sum()
        lda = last_data[:, a_cols]
        s2 = lda[lda.min(axis=1) >= 0].sum()
        assert s2 >= 0
        obs_attributes.append(
            (
                positions[cob],
                positions[x] if x is not None else positions[-1],
                l,
                1. * s2 / l,
            )
        )
        try:
            cob = x + 1
        except TypeError:  # fails for final x=None
            pass
    return contig_list


def balance_hidden_states(model, M):
    """
    Return break points [0, b_1, ..., b_M, oo) such that
    the probability of coalescing in each interval under the
    model is the same. (Breaks are returned in units of
    generations.)

    """
    import smcpp._smcpp

    M -= 1
    eta = smcpp._smcpp.PyRateFunction(model, [])
    ret = [0.0]
    # ms = np.arange(0.1, 2.1, .1).tolist() + list(range(3, M))
    ms = range(1, M)
    for m in ms:

        def f(t):
            Rt = float(eta.R(t))
            return np.exp(-Rt) - 1.0 * (M - m) / M

        a = b = ret[-1]
        while f(a) * f(b) >= 0:
            b = 2 * (b + 1)
        res = scipy.optimize.brentq(f, a, b)
        ret.append(res)
    ret.append(np.inf)
    return np.array(ret) * 2 * model.N0  # return in generations


def model_from_coal_probs(t, p, N0, pid):
    """
    Returns a piecewise constant model such that 

        P(Coal \in [t[i], t[i + 1])) = p[i]

    """
    Rt = 0
    t0 = t[0]
    a = []
    s = []
    for tt, pp in zip(t[1:-1], p[:-1]):
        # Rt1 = -np.log(np.exp(-Rt) - pp)
        # Rt1 = -np.log(np.exp(-Rt) * (1 - np.exp(Rt + np.log(pp))))
        Rt1 = Rt - np.log1p(-np.exp(Rt + np.log(pp)))
        s.append(tt - t0)
        a.append((Rt1 - Rt) / s[-1])
        Rt = Rt1
        t0 = tt
    s.append(1.)
    a.append(1.)
    return model.PiecewiseModel(a, s, N0, pid)


def calculate_t1(model, n, q):
    import smcpp._smcpp

    eta = smcpp._smcpp.PyRateFunction(model, [0., np.inf])
    c = n * (n - 1) / 2

    def f(t):
        return np.expm1(-c * eta.R(t)) + q

    return scipy.optimize.brentq(f, 0., model.knots[-1])


def _load_data_helper(fn):
    try:
        # This parser is way faster than np.loadtxt
        A = pd.read_csv(fn, sep=" ", comment="#", header=None).values
    except ImportError as e:
        logger.debug(e)
        A = np.loadtxt(fn, dtype=np.int32)
    except:
        logger.error("In file %s", fn)
        raise
    if len(A) == 0:
        raise RuntimeError("empty dataset: %s" % fn)
    with util.optional_gzip(fn, "rt") as f:
        first_line = next(f).strip()
        if first_line.startswith("# SMC++"):
            attrs = json.loads(first_line[7:])
            a = [len(a) for a in attrs["dist"]]
            n = [len(u) for u in attrs["undist"]]
            if "pids" not in attrs:
                raise RuntimeError("Data format is too old. Re-run VCF2SMC.")
        else:
            logger.error("Data file is not in SMC++ format: ", fn)
            sys.exit(1)
    pid = tuple(attrs["pids"])
    # Internally we always put the population with the distinguished lineage first.
    if len(a) == 2 and a[0] == 0 and a[1] == 2:
        n = n[::-1]
        a = a[::-1]
        pid = pid[::-1]
        A = A[:, [0, 4, 5, 6, 1, 2, 3]]
    data = np.ascontiguousarray(A, dtype="int32")
    return Contig(pid=pid, data=data, n=n, a=a, fn=fn)


def files_from_command_line_args(args):
    ret = []
    for f in args:
        if f[0] == "@":
            ret += [line.strip() for line in open(f[1:], "rt") if line.strip()]
        else:
            ret.append(f)
    return set(ret)


def load_data(files):
    with ProcessPoolExecutor(defaults.cores) as p:
        obs = list(p.map(_load_data_helper, files))
    return obs
