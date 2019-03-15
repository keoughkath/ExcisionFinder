#!/bin/bash
#$ -S /bin/bash
#$ -o /netapp/home/kathleen/1kgp_hg38_gens/logs/
#$ -e /netapp/home/kathleen/1kgp_hg38_gens/logs/
#$ -j y
#$ -cwd
#$ -l mem_free=4G
#$ -l arch=linux-x64
#$ -l netapp=1G,scratch=1G
#$ -l h_rt=336:00:00
#$ -t 300001-321185
#$ -tc 500

export TMPDIR=/scratch
echo SGE_TASK_ID
export locus=`sed "${SGE_TASK_ID}q;d" /pollard/home/kathleen/projects/AlleleAnalyzer/manuscript_analyses/1000genomes_analysis/dat/1kgp_hg38_regions.bed`
echo $locus
chrom=`echo $locus | cut -f1 -d" "`
start=`echo $locus | cut -f2 -d" "`
stop=`echo $locus | cut -f3 -d" "`
name=`echo $locus | cut -f4 -d" "`
export locus_formatted=${chrom}:${start}-${stop}
echo ${locus_formatted}
python /pollard/home/kathleen/projects/AlleleAnalyzer/preprocessing/generate_gens_dfs/get_gens_df.py -v /pollard/data/genetics/1kg/phase3/hg38/ALL.${chrom}_*.bcf ${locus_formatted} /pollard/data/projects/AlleleAnalyzer_data/1kgp_data/hg38_analysis/1kgp_formatted_variants/$name