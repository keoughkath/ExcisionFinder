# !/usr/bin/env python"""single_cut_targ_wtc_hg19 identifies allele-specific single cut excision sites. Written in Python version 3.6.1.Kathleen Keough et al 2017-2018.Usage:         single_cut_targ_wtc_hg19.py [-vs] <annots> <gene> <targdir> <cas_list> <bcf> <outdir> [--window=<window_in_bp>]        single_cut_targ_wtc_hg19.py -hArguments:    annots                           Gene annotations file (gene_annots_wsize) filepath.    gene                             Gene you would like to analyze.    targdir                          Directory where the variant targetability HDF5 files are stored.    cas_list                         Comma separated (no spaces!) list of Cas varieties to evaluate, options below.    outdir                           Directory to which you would like to write the output files.Options:    -h                               Show this screen.    -v                               Run as verbose, print to stderr.     -s                               Only consider sgRNA sites where variant is in a PAM (strict).    --window=<window_in_bp>          Window around the gene (in bp) to also consider [default: 0]. available Cas types = cpf1,SpCas9,SpCas9_VRER,SpCas9_EQR,SpCas9_VQR_1,SpCas9_VQR_2,StCas9,StCas9_2,SaCas9,SaCas9_KKH,nmCas9,cjCas9"""import pandas as pdfrom pandas import HDFStoreimport numpy as npfrom functools import reducefrom docopt import docoptimport itertoolsimport regex as reimport loggingimport subprocessfrom io import StringIOimport osimport time__version__ = '0.0.0'def load_gene_annots(annots_path):    """    Load gene annotation data (transcript data).    :param annots_path: str, filepath for gene_annots_wsize (Part of ExcisionFinder package).    :return: Refseq gene annotations file.    """    gene_annots = pd.read_csv(annots_path, sep='\t', header=0, names=['name', 'chrom', 'txStart', 'txEnd', 'cdsStart', 'cdsEnd', 'exonCount',       'exonStarts', 'exonEnds', 'size'])    return gene_annotsdef het(genotype):    """    Determine whether a genotype in format A|G is het.    :param genotype: genotype, str.    :return: bool, True = het, False = hom.    """    hap1, hap2 = re.split('/|\|',genotype)    return hap1 != hap2def next_exon(variant_position, coding_exon_starts):    """    get location of next coding exon after variant    :param coding_exon_starts: coding exon start positions, Pandas Series.    :param variant_position: chromosomal position, int    :return: chromosomal position of start of next coding exon, int    """    greater_than_var = [x for x in coding_exon_starts if x > variant_position]    if not greater_than_var:        return False    else:        next_coding_exon_pos = min(greater_than_var)        return next_coding_exon_posdef targ_pair(variant1, variant2, coding_positions, coding_exon_starts):    """    Determine whether a pair of variants positions is targetable based on whether they might    disrupt an exon.    :param variant1: position of variant 1, int.    :param variant2: position of variant 2, int.    :param coding_positions: coding positions, set.    :param coding_exon_starts: Start positions of coding exons, Pandas Series.    :return: whether targetable or not, bool.    """    low_var, high_var = sorted([variant1, variant2])    if low_var in coding_positions or high_var in coding_positions:        return True    else:        # checks whether larger variant position occurs in or after next exon        return bool(high_var >= next_exon(low_var, coding_exon_starts))def translate_gene_name(gene_name):    """    HDF5 throws all sort of errors when you have weird punctuation in the gene name, so    this translates it to a less offensive form.    """    repls = ('-', 'dash'), ('.', 'period')    trans_gene_name = reduce(lambda a, kv: a.replace(*kv), repls, str(gene_name))    return trans_gene_nameclass Gene:    """Holds information for the gene"""    def __init__(self, official_gene_symbol, gene_dat, window):        self.official_gene_symbol = official_gene_symbol        self.info = gene_dat.query("index == @self.official_gene_symbol")        self.n_exons = self.info["exonCount"].item()        self.coding_start = int(self.info["cdsStart"].item())        self.coding_end = int(self.info["cdsEnd"].item())        self.coding_exons = []        counter = 0        counterweight = len(list(                zip(                    list(map(int, self.info["exonStarts"].item().split(",")[:-1])),                    list(map(int, self.info["exonEnds"].item().split(",")[:-1])),                )))        for x in list(zip(list(map(int, self.info["exonStarts"].item().split(",")[:-1])),                      list(map(int, self.info["exonEnds"].item().split(",")[:-1])))):            counter += 1            if counter == 1:                self.coding_exons.append((max(self.coding_start, x[0]), x[1]))            elif counter == counterweight:                self.coding_exons.append((x[0], min(self.coding_end, x[1])))            else:                self.coding_exons.append((x[0], x[1]))        self.n_coding_exons = len(self.coding_exons)        self.start = self.info["txStart"].item() - window        self.end = self.info["txEnd"].item() + window        self.chrom = gene_dat.query("index == @self.official_gene_symbol")[            "chrom"        ].item()    def get_coding_positions_and_starts(self):        coding_positions = []        coding_exon_starts = []        for start, stop in self.coding_exons:            coding_positions.extend(list(range(start, stop + 1)))            coding_exon_starts.append(start)        return coding_positions, coding_exon_startsdef check_bcftools():    """     Checks bcftools version, and exits the program if the version is incorrect    """    version = subprocess.run("bcftools -v | head -1 | cut -d ' ' -f2", shell=True,\     stdout=subprocess.PIPE).stdout.decode("utf-8").rstrip()    if float(version) >= REQUIRED_BCFTOOLS_VER:        print(f'bcftools version {version} running')    else:         print(f"Error: bcftools must be >=1.5. Current version: {version}")        exit()def main(args):    annots = load_gene_annots(args['<annots>'])    gene = args['<gene>']    targ_df = args['<targdir>']    out_dir = args['<outdir>']    cas_list_append = args['<cas_list>'].split(',')    bcf = args['<bcf>']     window = int(args['--window'])    cas_list = ['all'] + cas_list_append    # define strictness level, which is whether or not variants near PAMs are considered    # along with those that are in PAMs    if args['-s']:        logging.info('Running as strict.')        strict_level = 'strict'    else:        strict_level = 'relaxed'        logging.info('Running as relaxed.')    logging.info('Now running ExcisionFinder on ' + gene + '.')    # grab info about relevant gene w/ class    MyGene = Gene(gene, annots, window)    # get number of coding exons in gene, must have at least 1 to continue    n_exons = MyGene.n_exons    n_coding_exons = MyGene.n_coding_exons    chrom = MyGene.chrom    coding_positions, coding_starts = MyGene.get_coding_positions_and_starts()    if n_coding_exons < 1:        logging.error(f'{n_exons} total exons in this gene, {n_coding_exons} of which are coding.\            No coding exons in gene {gene}, exiting.')        with open(f'{out_dir}no_coding_exons.txt','a+') as f:            f.write(gene + '\n')        exit()    else:        logging.info(f'{n_exons} total exons in this gene, {n_coding_exons} of which are coding.')    # load targetability information for each variant    targ_df = pd.read_hdf(targ_df, 'all', where=f'pos >= {MyGene.start} and pos <= {MyGene.end}')    # check whether there are annotated variants for this gene, abort otherwise    if targ_df.empty:        logging.error(f'No variants in file for gene {gene}')        with open(f'{out_dir}not_enough_hets.txt', 'a+') as fout:            fout.write(gene+'\n')        exit()    else:        logging.info(            f"Targetability data loaded, {targ_df.shape[0]} variants annotated in 1KGP for {gene}.")    # import region of interest genotypes    # bcf = f'{bcf}ALL.chr{chrom}.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.bcf' # this was for 1kgp    bcl_v = f'bcftools view -g "het" -r {chrom}:{MyGene.start}-{MyGene.end} -H {bcf}'    samples_cmd = f'bcftools query -l {bcf}'    bcl_samps = subprocess.Popen(samples_cmd, shell=True, stdout=subprocess.PIPE)    samples=bcl_samps.communicate()[0].decode("utf-8").split('\n')[:-1]    col_names = ['chrom','pos','rsid','ref','alt','score','random','info','gt'] + samples    bcl_view = subprocess.Popen(bcl_v, shell=True, stdout=subprocess.PIPE)    gens = pd.read_csv(StringIO(bcl_view.communicate()[0].decode("utf-8")),sep='\t',    header=None, names=col_names, usecols=['chrom','pos','ref','alt']+samples)    logging.info("Genotype(s) loaded.")    # heterozygous variants     het_gens = gens[samples].applymap(het).copy()    enough_hets = list(het_gens.sum(axis=0).loc[lambda s: s >= 1].index)    logging.info(str(len(enough_hets)) + ' individuals have >= 1 het positions.')    if len(enough_hets) < 1:        logging.info('No individuals have at least 1 het sites, aborting analysis.')        with open(f'{out_dir}not_enough_hets.txt', 'a+') as fout:            fout.write(gene+'\n')        exit()    logging.info('Checking targetability of individuals with sufficient number of hets.')    # set up targetability analyses    het_vars_per_ind = {}  # get heterozygous variant positions in coding exons for each individual    for ind in enough_hets:        het_vars_per_ind[ind] = gens.pos[het_gens[ind]][gens.pos.isin(coding_positions)].tolist()    # check targetability for each type of Cas    final_targ = pd.DataFrame({'sample':list(enough_hets)})    finaltargcols = [] # keeps track of columns for all cas types for later evaluating "all" condition    for cas in cas_list[1:]:  # skip all because is handled below faster        logging.info(f'Evaluating gene targetability for {cas}')        if args['-s']:            targ_vars_cas = targ_df.query(f'(makes_{cas}) or (breaks_{cas})').pos.tolist()        else:            targ_vars_cas = targ_df.query(f'(var_near_{cas}) or (makes_{cas}) or (breaks_{cas})').pos.tolist()        # figure out if individual has any targetable variants for this cas        ind_targ_cas = []        for ind in enough_hets:            if bool(set(targ_vars_cas).intersection(set(het_vars_per_ind[ind]))):                ind_targ_cas.append(True)            else:                ind_targ_cas.append(False)        finaltargcols.append(f'targ_{cas}')        final_targ[f'targ_{cas}'] = ind_targ_cas    # add column summarizing targetability across assessed Cas varieties    final_targ['targ_all'] = final_targ[finaltargcols].any(axis=1)    # HDF has issues with certain characters    translated_gene_name = translate_gene_name(gene)    # save to HDF     # make list of genes that actually get written to HDF5    with open(f'{out_dir}genes_evaluated.txt','a+') as f:        f.write(f'{translated_gene_name}\n')    # write gene dat to file    final_targ.to_hdf(f'{out_dir}{chrom}_results/{gene}.h5', 'all', comp_level=9,complib='blosc')    logging.info('Done!')if __name__ == '__main__':    arguments = docopt(__doc__, version=__version__)    if arguments['-v']:        logging.basicConfig(level=logging.INFO, format='[%(asctime)s %(name)s:%(levelname)s ]%(message)s')    else:        logging.basicConfig(level=logging.ERROR, format='[%(asctime)s %(name)s:%(levelname)s ]%(message)s')    logging.info(arguments)    main(arguments)