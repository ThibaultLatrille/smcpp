import scipy.optimize

import smcpp.model
from .optimizer_plugin import OptimizerPlugin, targets
from logging import getLogger

logger = getLogger(__name__)


class ScaleOptimizer(OptimizerPlugin):
    DISABLED = False

    def _f(self, alpha, x0, analysis):
        analysis.model[:] = x0 + alpha
        ret = float(analysis.Q())
        logger.debug("scale Q(%f)=%f", alpha, ret)
        return -ret

    @targets("pre M-step")
    def update(self, message, *args, **kwargs):
        analysis = kwargs["analysis"]
        x0 = analysis.model[:]
        res = scipy.optimize.minimize_scalar(
            self._f,
            args=(x0.astype("float"), analysis),
            method="bounded",
            bounds=(-1, 1),
        )
        analysis.model[:] = x0 + res.x
