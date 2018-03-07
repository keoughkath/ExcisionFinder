# !/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ExcisionFinder identifies allele-specific excision sites. Written in Python version 3.6.1.
Kathleen Keough 2017.

Usage: 
        ExcisionFinder.py [-v] <annots> <gene> <chromosome> <window> <targdir> <gensdir> <outdir> <high_scorers>
        ExcisionFinder.py -h

Arguments:
    annots            Gene annotations file (gene_annots_wsize) filepath.
    gene              Gene you would like to analyze.
    chromosome        Chromosome on which the gene is located.
    window            Window around the gene to include, in bp.
    targdir           Directory where the variant targetability HDF5 files are stored.
    gensdir           Directory where the explicit genotypes HDF5 files are stored.
    outdir            Directory to which you would like to write the output files.
    high_scorers      .npy list of variants that are high-scoring (specificity)

Options:
    -h                Show this screen.
    -v                Run as verbose, print to stderr. 
"""

import pandas as pd
import numpy as np
from functools import reduce
import sys
import os
from docopt import docopt
import itertools
import logging

__version__ = '0.0.0'

CAS_LIST = ['all', 'SaCas9_KKH']


def load_gene_annots(annots_path):
    """
    Load gene annotation data (transcript data).
    :param annots_path: str, filepath for gene_annots_wsize (Part of ExcisionFinder package).
    :return: Refseq gene annotations file.
    """
    gene_annots = pd.read_csv(annots_path, sep='\t', header=0, names=['name', 'chrom', 'txStart', 'txEnd',
                                                                      'cdsStart', 'cdsEnd', 'exonCount',
                                                                      'exonStarts', 'exonEnds', 'gene_name',
                                                                      'size']).sort_values(by='size', ascending=False)
    return gene_annots


def get_canonical(gene_name, gene_annots, outdir=' '):
    """
    Get the canonical (longest) transcript of gene.
    :param gene_name: HUGO gene ID, str.
    :param gene_annots: refseq gene annotations, Pandas dataframe.
    :param outdir: directory to save files outputted by this program, str.
    :return: refseq transcript ID, str.
    """
    try:
        canonical_transcript = gene_annots.query('gene_name == @gene_name').name.iloc[0]
        logging.info(canonical_transcript)
        return canonical_transcript
    except IndexError:
        logging.error(f'No transcript found - skipping {gene_name}.')
        with open(os.path.join(outdir, "no_transcript.txt"), "a") as genefile:
            genefile.write(gene_name + '\n')
        exit()


def get_coding_exons(transcript_id, gene_annots, gene, outdir=''):
    """
    This function assembles a dataframe labeling which exons are coding,
    and extracts annotated variants for those coding exons.
    :param transcript_id: Refseq transcript ID, str.
    :param gene_annots: gene annotations, Pandas dataframe.
    :param gene: HUGO name of input gene, str.
    :param outdir: directory to which logfiles are saved.
    :return: exon_status_df, n_coding_exons, start_coord, stop_coord, coding_range, ce_starts.
    """
    exon_starts = set(map(int, gene_annots.query('name == @transcript_id').exonStarts.iloc[0].split(',')[:-1]))
    exon_stops = set(map(int, gene_annots.query('name == @transcript_id').exonEnds.iloc[0].split(',')[:-1]))
    cds_start = gene_annots.query('name == @transcript_id').cdsStart.iloc[0]
    cds_end = gene_annots.query('name == @transcript_id').cdsEnd.iloc[0]
    coding_region = set(range(cds_start, cds_end + 1))

    # determine which exon starts are for coding exons

    ce_starts_list = list(exon_starts.intersection(coding_region))
    ce_starts = pd.Series(ce_starts_list)
    n_coding_exons = ce_starts.shape[0]

    # continues only if there is at least one coding exon

    if n_coding_exons == 0:
        logging.info(f'No coding exons in {gene}.')
        with open(os.path.join(outdir, 'no_coding_exons.txt'), "a") as genefile:
            genefile.write(gene + '\n')
        sys.exit()
    else:
        logging.info(f'{n_coding_exons} coding exons in {gene}.')

    exon_status_df = pd.DataFrame()
    exon_status_df['exonStarts'] = sorted(list(exon_starts))

    def coding_exon(exon_start_value):
        return exon_start_value in ce_starts_list

    exon_status_df['codingStatus'] = np.vectorize(coding_exon)(exon_status_df.exonStarts)
    exon_status_df['exon'] = exon_status_df.index + 1
    exon_status_df['start_hg19'] = sorted(list(exon_starts))
    exon_status_df['stop_hg19'] = sorted(list(exon_stops))

    start_coord = gene_annots.query('gene_name == @gene').sort_values(by='size', ascending=False).txStart.iloc[0]
    stop_coord = gene_annots.query('gene_name == @gene').sort_values(by='size', ascending=False).txEnd.iloc[0]

    # define coding range (coding positions)

    coding_df = exon_status_df.query('codingStatus')

    # get set of all coding positions in this gene for later use

    coding_range = []
    for index, row in coding_df.iterrows():
        coding_range.extend(list(range(row['start_hg19'], row['stop_hg19'] + 1)))
    coding_range = set(coding_range)

    return exon_status_df, n_coding_exons, start_coord, stop_coord, coding_range, ce_starts


def het(genotype):
    """
    Determine whether a genotype in format A|G is het.
    :param genotype: genotype, str.
    :return: bool, True = het, False = hom.
    """
    hap1, hap2 = str(genotype).split('|')
    return hap1 != hap2


def next_exon(variant_position, coding_exon_starts):
    """
    get location of next coding exon after variant
    :param coding_exon_starts: coding exon start positions, Pandas Series.
    :param variant_position: chromosomal position, int
    :return: chromosomal position of start of next coding exon, int
    """
    greater_than_var = coding_exon_starts[coding_exon_starts > variant_position]
    if greater_than_var.empty:
        return False
    else:
        next_coding_exon_pos = greater_than_var.min()
        return next_coding_exon_pos


def targ_pair(variant1, variant2, coding_positions, coding_exon_starts):
    """
    Determine whether a pair of variants positions is targetable based on whether they might
    disrupt an exon.
    :param variant1: position of variant 1, int.
    :param variant2: position of variant 2, int.
    :param coding_positions: coding positions, set.
    :param coding_exon_starts: Start positions of coding exons, Pandas Series.
    :return: whether targetable or not, bool.
    """
    low_var, high_var = sorted([variant1, variant2])
    if low_var in coding_positions or high_var in coding_positions:
        return True
    else:
        # checks whether larger variant position occurs in or after next exon
        return bool(high_var >= next_exon(low_var, coding_exon_starts))


def has_targ_pair(het_positions, targ_pairs):
    """
    Determine whether an individual ind has at least one targetable pairs of variants.
    :param het_positions: Heterozygous positions for this individual, set.
    :param targ_pairs: Targetable pairs in this region, tuple.
    :return: bool of whether pair is targetable
    """
    targ_status = False
    for x in targ_pairs:
        if set(x).issubset(het_positions):
            targ_status = True
            break
    return targ_status


def relevant_columns(cas='all'):
    """
    Obtains relevant columns from variant targetability df.
    :param cas: Cas variety, str.
    :return: lists of each relevant column. f
    """
    if cas == 'all':
        make_cols = list(f'makes_{cas}' for cas in CAS_LIST[1:])
        break_cols = list(f'breaks_{cas}' for cas in CAS_LIST[1:])
        near_cols = list(f'var_near_{cas}' for cas in CAS_LIST[1:])
    else:
        make_cols = [f'makes_{cas}']
        break_cols = [f'breaks_{cas}']
        near_cols = [f'var_near_{cas}']
    return make_cols, break_cols, near_cols


def targetable_haplotypes(targ_data, make_cols, break_cols, near_cols):
    """
    Determines whether both or either haplotype is allele-specifically targetable due to the variant.
    :param targ_data: relevant row from variant targetability data for the position and variant, Pandas Dataframe row.
    :param make_cols: relevant columns based on type of cas, list.
    :param break_cols: relevant columns based on type of cas, list.
    :param near_cols: relevant columns based on type of cas, list.
    :return: whether the haplotype with the variant (targ_alt_hap) and/or the reference haplotype(s) is/are targetable,
    bool.
    """
    targ_alt_hap = False
    targ_ref_hap = False
    if (targ_data[make_cols]).any().any():
        targ_alt_hap = True
    if (targ_data[break_cols]).any().any():
        targ_ref_hap = True
    if (targ_data[near_cols]).any().any():
        targ_alt_hap = True
        targ_ref_hap = True
    return targ_alt_hap, targ_ref_hap


def targ_haps_both_alt(rel_row_1, rel_row_2, make_cols, break_cols, near_cols):
    """
    This function is only used if the individual is non-reference at both alleles heterozygously, i.e.
    both alleles are variants, but different variants. 
    :param rel_row_1: relevant row from variant targetability data for the position and variant for alt
    allele on haplotype 1, Pandas Dataframe row.
    :param rel_row_2: relevant row from variant targetability data for the position and variant for alt
    allele on haplotype 2, Pandas Dataframe row.
    :param make_cols: relevant columns based on type of cas, list.
    :param break_cols: relevant columns based on type of cas, list.
    :param near_cols: relevant columns based on type of cas, list.
    :return: whether each of haplotype 1 and 2 are targetable. 
    """
    targ_alt1_hap, targ_alt2_hap = targetable_haplotypes(rel_row_1, make_cols, break_cols, near_cols)
    if targ_alt1_hap and targ_alt2_hap:
        return targ_alt1_hap, targ_alt2_hap
    else:
        targ_alt1_hap2, targ_alt2_hap2 = targetable_haplotypes(rel_row_2, make_cols, break_cols, near_cols)
        targ_alt1_hap_out = any([targ_alt1_hap, targ_alt1_hap2])
        targ_alt2_hap_out = any([targ_alt2_hap, targ_alt2_hap2])
        return targ_alt1_hap_out, targ_alt2_hap_out


def haplotype_targetability(position, sample, chrdf, roigens_hdf, cas_type='all'):
    """
    Determines whether each haplotype at this positions is targetable in the given individual.
    :param position: chromosomal position of the variant in question, int.
    :param sample: individual ID, str.
    :param chrdf: variant targetability data, Pandas dataframe.
    :param roigens_hdf: explicit genotype data, Pandas dataframe.
    :param cas_type: Cas variety.
    :return: 2 bools indicating targetability of hap 1 and hap 2, respectively.
    """
    while True:
        try:
            genotype = roigens_hdf.query('index == @position')[sample].item()
            hap1_allele, hap2_allele = genotype.split('|')
            ref = chrdf.query('pos == @position').ref.item()
            make_cols, break_cols, near_cols = relevant_columns(cas_type)
            if hap2_allele == ref:
                relevant_row = chrdf.query('(pos == @position) & (alt == @hap1_allele)')
                targ_hap1, targ_hap2 = targetable_haplotypes(relevant_row, make_cols, break_cols, near_cols)
                return targ_hap1, targ_hap2
            elif hap1_allele == ref:
                relevant_row = chrdf.query('(pos == @position) & (alt == @hap2_allele)')
                targ_hap2, targ_hap1 = targetable_haplotypes(relevant_row, make_cols, break_cols, near_cols)
                return targ_hap1, targ_hap2
            elif hap1_allele != ref and hap2_allele != ref:
                rel_row_hap1 = chrdf.query('(pos == @position) & (alt == @hap1_allele)')
                rel_row_hap2 = chrdf.query('(pos == @position) & (alt == @hap2_allele)')
                targ_hap1, targ_hap2 = targ_haps_both_alt(rel_row_hap1, rel_row_hap2, make_cols, break_cols, near_cols)
                return targ_hap1, targ_hap2
            else:
                logging.error(f'Something is wrong. Check {sample} at {position}. Exiting.')
                exit()
        except ValueError:
            return False, False


def has_targ_pos_pair_same_hap(sample, cas_type, targ_pairs, targ_haps_df):
    """
    Figure out if person has at least one pair of hets at targetable positions
    on same haplotype.
    :param sample: Individual ID, str.
    :param cas_type: Cas variety, str.
    :param targ_pairs: targetable pairs in this gene, tuple.
    :return: Whether person meets condition described above, bool.
    """
    hap1_col = f'hap1_{cas_type}'
    hap2_col = f'hap2_{cas_type}'
    sample_hets_hap1 = set(targ_haps_df[targ_haps_df[hap1_col]].query('sample == @sample').pos)
    sample_hets_hap2 = set(targ_haps_df[targ_haps_df[hap2_col]].query('sample == @sample').pos)
    if has_targ_pair(sample_hets_hap1, targ_pairs):
        return True
    elif has_targ_pair(sample_hets_hap2, targ_pairs):
        return True
    else:
        return False


def translate_gene_name(gene_name):
    """
    HDF5 throws all sort of errors when you have weird punctuation in the gene name, so
    this translates it to a less offensive form.
    """
    repls = ('-', 'dash'), ('.', 'period')
    trans_gene_name = reduce(lambda a, kv: a.replace(*kv), repls, str(gene_name))
    return trans_gene_name


def main(args):

    annots = load_gene_annots(args['<annots>'])
    gene = args['<gene>']
    chrom = args['<chromosome>']
    targ_dir = args['<targdir>']
    gens_dir = args['<gensdir>']
    out_dir = args['<outdir>']
    window = int(args['<window>'])
    high_scorers = np.load(args['<high_scorers>']).tolist()
    min_high = min(high_scorers)
    max_high = max(high_scorers)


    logging.info('Now running ExcisionFinder on ' + gene + '.')

    os.makedirs(out_dir, exist_ok=True)

    # FIRST DROPOUT POINT #

    geneid = get_canonical(gene, annots, out_dir)

    logging.info(f'Transcript used for this gene is {geneid}.')

    # get coding exon data
    # SECOND DROPOUT POINT #

    exons, n_coding, genestart, genestop, coding_positions, coding_exon_starts = get_coding_exons(str(geneid),
                                                                                                  annots, gene,
                                                                                                  out_dir)
    n_exons = exons.shape[0]
    genestart = float(genestart - window)
    genestop = float(genestop + window)
    # print(type(genestop))

    logging.info(f'{n_exons} total exons in this gene, {n_coding} of which are coding.')

    # load targetability information for each variant

    # chrdf = pd.read_hdf(f'{targ_dir}chr{chrom}_targ.hdf5', 'all', where='pos <= genestop & pos >= genestart')

    chrdf_prelim = pd.read_hdf(os.path.join(targ_dir, f'chr{chrom}_targ.hdf5'), where=f'pos <= {genestop} and pos >= {genestart}').query('pos in @high_scorers')
    chrdf = chrdf_prelim.iloc[:, range(6, 42)].applymap(bool)
    chrdf['pos'] = chrdf_prelim['pos']
    chrdf['ref'] = chrdf_prelim['ref']
    chrdf['alt'] = chrdf_prelim['alt']

    # chr_hdf = pd.HDFStore(os.path.join(targ_dir, f'chr{chrom}_targ.hdf5'))

    # chrdf = pd.DataFrame()

    # for chunk in chr_hdf.select('all', chunksize=50000, where=f'pos <= @genestop and pos >= @genestart'):
    #     chunk.iloc[:, range(6, 42)] = chunk.iloc[:, range(6, 42)].applymap(bool)
    #     chrdf = chrdf.append(chunk)

    # check whether there are annotated variants for this gene, abort otherwise
    # THIRD DROPOUT POINT #

    if chrdf.empty:
        logging.error(f'No variants in 1KGP for gene {gene}')
        with open(os.path.join(out_dir,f'not_enough_hets.txt'), 'a') as fout:
            fout.write(gene+'\n')
        exit()
    else:
        logging.info("Targetability data loaded.")

    # import region of interest genotypes

    # roigens = pd.read_hdf(os.path.join(gens_dir,f'chr{chrom}_gens.hdf5'), where='index <= genestop & index
    #  >= genestart')

    # logging.info("1KGP genotypes loaded.")

    # hetroigens = roigens.applymap(het)

    # gens_hdf = pd.HDFStore(os.path.join(gens_dir, f'chr{chrom}_gens.hdf5'))
    gens_hdf = pd.read_hdf(os.path.join(gens_dir, f'chr{chrom}_gens.hdf5'), where=f'index <= {genestop} and index >= {genestart}').query('index in @high_scorers')
    hetroigens = pd.read_hdf(os.path.join(gens_dir, f'chr{chrom}_gens.hdf5'), where=f'index <= {genestop} and index >= {genestart}').query('index in @high_scorers').applymap(het)

    # hetroigens = pd.DataFrame()

    # genestop = float(genestop)
    # genestart = float(genestart)

    # for chunk in gens_hdf.select('all', chunksize=5000, where='index <= @genestop and index >= @genestart and index in @high_scorers'):
        # hetroigens = hetroigens.append(chunk.applymap(het))

    logging.info('Genotype data loaded.')

    enough_hets = list(hetroigens.sum(axis=0).loc[lambda s: s >= 2].index)

    logging.info(str(len(enough_hets)) + ' individuals have >= 2 het positions.')

    # if no individuals have at least 2 het variants, abort
    # FOURTH DROPOUT POINT #

    if len(enough_hets) < 1:
        logging.info('No individuals have at least 2 het sites, aborting analysis.')
        with open(os.path.join(out_dir, 'not_enough_hets.txt'), 'a') as fout:
            fout.write(gene+'\n')
        exit()

    # get variants in region

    variants = sorted(hetroigens.index)

    # set up targetability analyses

    het_vars_per_ind = {}  # get heterozygous variant positions for each individual

    for ind in enough_hets:
        het_vars_per_ind[ind] = hetroigens.index[hetroigens[ind]]

    val_occurrences = []  # steps towards longform dataframe with one row per ind/het combo

    for val in het_vars_per_ind.values():
        val_occurrences.append(val.shape[0])

    targ_haps = pd.DataFrame({'sample': np.repeat(list(het_vars_per_ind.keys()), val_occurrences),
                              'pos': list(itertools.chain.from_iterable(het_vars_per_ind.values()))})

    # get variant combinations and extract targetable pairs

    logging.info('Getting variant combos.')

    variant1 = []
    variant2 = []

    for var1, var2 in itertools.product(variants, variants, repeat=1):
        if (var1 != var2) and (max([var1,var2]) <= min([var1,var2])+10000) and (targ_pair(var1, var2, coding_positions, coding_exon_starts)):
            variant1.append(var1)
            variant2.append(var2)
        else:
            continue

    logging.info('Combos obtained.')

    # below is how I would like to generate variant combinations, but is too memory-intensive for large genes

    # var_targ_df = pd.DataFrame(pd.core.reshape.util.cartesian_product([variants, variants])).T
    # var_targ_df.columns = ['variant1', 'variant2']

    # var_targ_df['targetable'] = var_targ_df.eval('(variant1 in coding positions or variant 2 in coding positions) or '
    #                                              '(variant2 >= next_exon(variant1)')

    #targ_pairs = var_targ_df.query('targetable')
    targ_pairs_tuple = tuple(zip(variant1, variant2))
    eligible_variants = set(variant1 + variant2)

    targ_haps = targ_haps.query('pos in @eligible_variants')

    if targ_haps.empty:
        logging.info(f'No targetable individuals for {gene}')
        with open(os.path.join(out_dir, 'no_targetable_inds.txt'), 'a') as fout:
            fout.write(gene+'\n')
        exit()

    logging.info('Checking targetability of individuals with sufficient number of hets.')

    final_targ = pd.DataFrame({'sample': enough_hets})

    finaltargcols = []

    for cas in CAS_LIST[1:]:  # skip all because is handled below faster
        logging.info(f'Evaluating gene targetability for {cas}')
        targ_haps[f'hap1_{cas}'], targ_haps[f'hap2_{cas}'] = \
            zip(*targ_haps.apply(lambda row: haplotype_targetability(row['pos'], row['sample'], chrdf, gens_hdf,
                                                                     cas_type=cas), axis=1))
        final_targ['targ_' + str(cas)] = final_targ.apply(lambda row: has_targ_pos_pair_same_hap(row['sample'], cas, 
                                                                                                 targ_pairs_tuple,
                                                                                                 targ_haps), axis=1)
        finaltargcols.append(f'targ_{cas}')

    final_targ['targ_all'] = final_targ[finaltargcols].any(axis=1)
    logging.info(f'Saving output to {os.path.join(out_dir,"chr"+chrom+"_out.hdf5")}')
    hdf = pd.HDFStore(os.path.join(out_dir,f'chr{chrom}_out.hdf5'), mode='a', complib='blosc')
    hdf.put(translate_gene_name(gene), final_targ)
    other_hdf = pd.HDFStore(os.path.join(out_dir,f'hap_targ_ind_{chrom}.hdf5'), mode='a', complib='blosc')
    other_hdf.put(translate_gene_name(gene), targ_haps)
    logging.info('Done!')


if __name__ == '__main__':
    arguments = docopt(__doc__, version=__version__)
    if arguments['-v']:
        logging.basicConfig(level=logging.INFO, format='[%(asctime)s %(name)s:%(levelname)s ]%(message)s')
    else:
        logging.basicConfig(level=logging.ERROR, format='[%(asctime)s %(name)s:%(levelname)s ]%(message)s')
    logging.info(arguments)
    main(arguments)