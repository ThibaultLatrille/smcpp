#!/bin/bash -x
SMC=$1
set -e
$SMC vcf2smc example/example.vcf.gz /tmp/example.1.smc.gz 1 msp1:msp_0,msp_1
$SMC vcf2smc -d msp_2 msp_2 example/example.vcf.gz /tmp/example.2.smc.gz 1 msp2:msp_2
$SMC vcf2smc -d msp_1 msp_1 example/example.vcf.gz /tmp/example.12.smc.gz 1 msp1:msp_0,msp_1 msp2:msp_2
$SMC estimate -v -o /tmp/out/1 --theta .00025 --fold --em-iterations 1 /tmp/example.1.smc.gz
$SMC estimate -p 0.01 -v -o /tmp/out/2 --theta .00025 --em-iterations 1 /tmp/example.2.smc.gz
$SMC estimate -v -o /tmp/out/12 --theta .00025 --em-iterations 1 /tmp/example.12.smc.gz
$SMC split -v -o /tmp/out/split --em-iterations 1 \
    /tmp/out/1/model.final.json \
    /tmp/out/2/model.final.json \
    /tmp/example.*.smc.gz
$SMC split -v --fold -o /tmp/out/split --em-iterations 1 \
    /tmp/out/1/model.final.json \
    /tmp/out/2/model.final.json \
    /tmp/example.*.smc.gz
$SMC posterior --fold --colorbar -vv --plot /tmp/plot.png \
    /tmp/out/1/model.final.json \
    /tmp/example.1.smc.gz /tmp/matrix.npz
$SMC plot -c -g 29 --logy /tmp/1.png /tmp/out/1/model.final.json
$SMC plot /tmp/2.pdf /tmp/out/2/model.final.json
$SMC plot -c --logy /tmp/12.png /tmp/out/12/model.final.json
$SMC plot -c --logy /tmp/all.pdf /tmp/out/*/model.final.json
