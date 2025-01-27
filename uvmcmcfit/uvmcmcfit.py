#!/usr/bin/env python
"""
 Author: Shane Bussmann, T. K. Daisy Leung, Aaron Beyer, Christof Buchbender


Similar to uvmcmcfit.py, but here we edited it to
    - throw out a pre-defined number of burn-in samples;
    - save acceptance_fraction + misc stuff as a separate file
    - only save samples every some number of samples (instead of every iteration)
    - email ourselves once a certain number of samples have been obtained, and so we can decide whether or not to stop sampling instead of interupting the code


 Last modified: 2023 Sep 21

 Note: This is experimental software that is in a very active stage of
 development.  If you are interested in using this for your research, please
 contact me first at tleung@astro.cornell.edu!  Thanks.

 Purpose: Fit a parametric model to interferometric data using Dan
 Foreman-Mackey's emcee routine.  Gravitationally lensed sources are accounted
 for using ray-tracing routines based on Adam Bolton's lensdemo_script and
 lensdemo_func python scripts.  Here is the copyright license from
 lensdemo_script.py:

 Copyright 2009 by Adam S. Bolton
 Creative Commons Attribution-Noncommercial-ShareAlike 3.0 license applies:
 http://creativecommons.org/licenses/by-nc-sa/3.0/
 All redistributions, modified or otherwise, must include this
 original copyright notice, licensing statement, and disclaimer.
 DISCLAIMER: ABSOLUTELY NO WARRANTY EXPRESS OR IMPLIED.
 AUTHOR ASSUMES NO LIABILITY IN CONNECTION WITH THIS COMPUTER CODE.

--------------------------
 USAGE

 python $PYSRC/uvmcmcfit2.py

--------------------------
 SETUP PROCEDURES

 1. Establish a directory that contains data for the specific target for which
 you wish to measure a lens model.  This is the directory from which you will
 run the software.

 I call this "uvfit00" for the first run on a given dataset, "uvfit01" for
 the second, etc.

 2. Inside this directory, you must ensure the following files are present:

 - "config.yaml": This is the configuration file that describes where the source
 of interest is located, what type of model to use for the lens and source, the
 name of the image of the target from your interferometric data, the name of
 the uvfits files containing the interferometric visibilities, and a few
 important processing options as well.  Syntax is yaml.

 - Image of the target from your interferometric data.  The spatial resolution
 of this image (arcseconds per pixel), modified by an optional oversampling
 parameter, defines the spatial resolution in both the unlensed and lensed
 surface brightness maps.

 - interferometric visibilities for every combination of array configuration,
 sideband, and date observed that you want to model.

 3. More info about the constraints and priors input files.

 - Lenses: The lenses are assumed to have singular isothermal ellipsoid
 profiles.

 - Sources: Sources are represented by Gaussian profiles.
 
 4. If you want to run this in CASA (not fully tested) then do as follows:
 		- within casa: from uvmcmcfit.uvmcmcfit import main
 		- main()

--------
 OUTPUTS

 "posteriorpdf.fits": model parameters for every MCMC iteration, in fits
 format.

 "summary.txt": contains mean acceptance fraction

"""


# import the required modules
import os
import os.path
import sys
import time

# from tqdm import tqdm
from subprocess import call

import emcee

# import lensutil
import numpy
import yaml
from astropy.io import fits
from astropy.table import Table

# import pyximport
# pyximport.install(setup_args={"include_dirs":numpy.get_include()})
from . import (
    lensutil,
    sample_vis,
    setuputil,
    uvutil,
)

# cwd = os.getcwd()
# sys.path.append(cwd)
# import config


def lnprior(pzero_regions, paramSetup):
    """

    Function that computes the ln prior probabilities of the model parameters.

    """
    priorln = 0.0
    mu = 1

    #     import pdb; pdb.set_trace()

    # ensure all parameters are finite
    if (pzero_regions * 0 != 0).any():
        priorln = -numpy.inf
        return priorln, mu

    # Uniform priors
    uniform_regions = paramSetup["PriorShape"] == "Uniform"
    if uniform_regions.any():
        p_l_regions = paramSetup["p_l"][uniform_regions]
        p_u_regions = paramSetup["p_u"][uniform_regions]
        pzero_uniform = pzero_regions[uniform_regions]
        if (pzero_uniform > p_l_regions).all() and (pzero_uniform < p_u_regions).all():
            # log prior
            priorln += numpy.log(1.0 / numpy.abs(p_l_regions - p_u_regions)).sum()
        else:
            priorln = -numpy.inf
            return priorln, mu

    # Gaussian priors
    gaussian_regions = paramSetup["PriorShape"] == "Gaussian"
    if gaussian_regions.any():
        import scipy.stats as stats

        # initlized as [mean, blah, blah, sigma]
        mean_regions = paramSetup["p_l"][gaussian_regions]
        rms_regions = paramSetup["p_u"][gaussian_regions]
        pzero_gauss = pzero_regions[gaussian_regions]
        priorln += numpy.log(
            stats.norm(scale=rms_regions, loc=mean_regions).pdf(pzero_gauss)
        ).sum()

    # Gaussian pos (for parameter that must be positive e.g. flux density)
    gaussPos_regions = paramSetup["PriorShape"] == "GaussianPos"
    if gaussPos_regions.any():
        pzero_gaussPos = pzero_regions[gaussPos_regions]
        if pzero_gaussPos < 0.0:
            priorln = -numpy.inf
            return priorln, mu
        else:
            import scipy.stats as stats

            # initlized as [mean, blah, blah, sigma]
            mean_regions = paramSetup["p_l"][gaussPos_regions]
            rms_regions = paramSetup["p_u"][gaussPos_regions]
            priorln += numpy.log(
                stats.norm(scale=rms_regions, loc=mean_regions).pdf(pzero_gauss)
            ).sum()

    #     if not isinstance(priorln, float):
    #         priorln = priorln.sum()
    return priorln, mu


def lnlike(
    pzero_regions,
    vis_complex,
    wgt,
    uuu,
    vvv,
    pcd,
    fixindx,
    paramSetup,
    computeamp=True,
    miriad=False,
):
    """Function that computes the Ln likelihood of the data"""

    # search poff_models for parameters fixed relative to other parameters
    fixindx = numpy.array(fixindx)
    fixed = (numpy.where(fixindx >= 0))[0]
    nfixed = fixindx[fixed].size
    p_u_regions = paramSetup["p_u"]
    poff_regions = p_u_regions.copy()
    poff_regions[:] = 0.0
    # for ifix in range(nfixed):
    #    poff_regions[fixed[ifix]] = pzero_regions[fixindx[fixed[ifix]]]
    for ifix in range(nfixed):
        ifixed = int(fixed[ifix])
        subindx = int(fixindx[ifixed])
        par0 = 0
        if fixindx[subindx] > 0:
            par0 = pzero_regions[fixindx[subindx]]
        poff_regions[ifixed] = pzero_regions[subindx] + par0

    parameters_regions = pzero_regions + poff_regions

    npar_previous = 0

    amp = []  # Will contain the 'blobs' we compute
    g_image_all = 0.0
    g_lensimage_all = 0.0
    e_image_all = 0.0
    e_lensimage_all = 0.0

    nregions = paramSetup["nregions"]
    for regioni in range(nregions):
        # get the model info for this model
        x = paramSetup["x"][regioni]
        y = paramSetup["y"][regioni]
        headmod = paramSetup["modelheader"][regioni]
        nlens = paramSetup["nlens_regions"][regioni]
        nsource = paramSetup["nsource_regions"][regioni]
        model_types = paramSetup["model_types"][regioni]

        # get pzero, p_u, and p_l for this specific model
        nparlens = 5 * nlens
        nparsource = 6 * nsource
        npar = nparlens + nparsource + npar_previous
        parameters = parameters_regions[npar_previous:npar]
        npar_previous = npar

        # -----------------------------------------------------------------
        # Create a surface brightness map of lensed emission for the given set
        # of foreground lens(es) and background source parameters.
        # -----------------------------------------------------------------

        g_image, g_lensimage, e_image, e_lensimage, amp_tot, amp_mask = lensutil.sbmap(
            x, y, nlens, nsource, parameters, model_types, computeamp=computeamp
        )
        e_image_all += e_image
        e_lensimage_all += e_lensimage
        g_image_all += g_image
        g_lensimage_all += g_lensimage
        amp.extend(amp_tot)
        amp.extend(amp_mask)

        # --------------------------------------------------------------------
        # Python version of UVMODEL:
        # "Observe" the lensed emission with the interferometer
        # --------------------------------------------------------------------

        if nlens > 0:
            if computeamp:
                # Evaluate amplification for each region
                lensmask = e_lensimage != 0
                mask = e_image != 0
                numer = g_lensimage[lensmask].sum()
                denom = g_image[mask].sum()
                amp_mask = numer / denom
                numer = g_lensimage.sum()
                denom = g_image.sum()
                amp_tot = numer / denom
                if amp_tot > 1e2:
                    amp_tot = 1e2
                if amp_mask > 1e2:
                    amp_mask = 1e2
                amp.extend([amp_tot])
                amp.extend([amp_mask])
            else:
                amp.extend([1.0])
                amp.extend([1.0])

    if miriad:
        # save the fits image of the lensed source
        ptag = str(os.getpid())
        SBmapLoc = "LensedSBmap" + ptag + ".fits"
        fits.writeto(SBmapLoc, g_lensimage_all, header=headmod, clobber=True)

        # convert fits format to miriad format
        SBmapMiriad = "LensedSBmap" + ptag + ".miriad"
        os.system("rm -rf " + SBmapMiriad)
        cmd = "fits op=xyin in=" + SBmapLoc + " out=" + SBmapMiriad
        call(cmd + " > /dev/null 2>&1", shell=True)

        # compute simulated visibilities
        modelvisfile = "SimulatedVisibilities" + ptag + ".miriad"
        call("rm -rf " + modelvisfile, shell=True)
        cmd = (
            "uvmodel options=subtract vis="
            + visfilemiriad
            + " model="
            + SBmapMiriad
            + " out="
            + modelvisfile
        )
        call(cmd + " > /dev/null 2>&1", shell=True)

        # convert simulated visibilities to uvfits format
        mvuvfits = "SimulatedVisibilities" + ptag + ".uvfits"
        call("rm -rf " + mvuvfits, shell=True)
        cmd = "fits op=uvout in=" + modelvisfile + " out=" + mvuvfits
        call(cmd + " > /dev/null 2>&1", shell=True)

        # read simulated visibilities
        mvuv = fits.open(mvuvfits)
        diff_real = mvuv[0].data["DATA"][:, 0, 0, 0, 0, 0]
        diff_imag = mvuv[0].data["DATA"][:, 0, 0, 0, 0, 1]
        wgt = mvuv[0].data["DATA"][:, 0, 0, 0, 0, 2]
        # model_complex = model_real[goodvis] + 1.0j * model_imag[goodvis]
        diff_all = numpy.append(diff_real, diff_imag)
        wgt = numpy.append(wgt, wgt)
        goodvis = wgt > 0
        diff_all = diff_all[goodvis]
        wgt = wgt[goodvis]
        chi2_all = wgt * diff_all * diff_all
    else:
        model_complex = sample_vis.uvmodel(g_lensimage_all, headmod, uuu, vvv, pcd)
        diff_all = numpy.abs(vis_complex - model_complex)
        chi2_all = wgt * diff_all * diff_all
    # model_real += numpy.real(model_complex)
    # model_imag += numpy.imag(model_complex)

    # fits.writeto('g_lensimage.fits', g_lensimage_all, headmod, clobber=True)
    # import matplotlib.pyplot as plt
    # print(pzero_regions)
    # plt.imshow(g_lensimage, origin='lower')
    # plt.colorbar()
    # plt.show()
    # plt.imshow(g_image, origin='lower')
    # plt.colorbar()
    # plt.show()

    # calculate chi^2 assuming natural weighting
    # fnuisance = 0.0
    # modvariance_real = 1 / wgt #+ fnuisance ** 2 * model_real ** 2
    # modvariance_imag = 1 / wgt #+ fnuisance ** 2 * model_imag ** 2
    # wgt = wgt / 4.
    # chi2_real_all = (real - model_real) ** 2. / modvariance_real
    # chi2_imag_all = (imag - model_imag) ** 2. / modvariance_imag
    # chi2_all = numpy.append(chi2_real_all, chi2_imag_all)

    # compute the sigma term
    # sigmaterm_real = numpy.log(2 * numpy.pi / wgt)
    # sigmaterm_imag = numpy.log(2 * numpy.pi * modvariance_imag)

    # compute the ln likelihood
    lnlikemethod = paramSetup["lnlikemethod"]
    if lnlikemethod == "chi2":
        lnlike = chi2_all
    else:
        # by definition, loglike = -n/2*ln(2pi sigma^2) - 1/(2sigma^2) sum of (data-model)^2 over i=1 to n; but the constant term doesn't matter
        sigmaterm_all = len(wgt) * numpy.log(2 * numpy.pi / wgt)
        lnlike = chi2_all  # + sigmaterm_all
        # * -1/2 factor in latter step

    # compute number of degrees of freedom
    # nmeasure = lnlike.size
    # nparam = (pzero != 0).size
    # ndof = nmeasure - nparam

    # assert that lnlike is equal to -1 * maximum likelihood estimate
    # use visibilities where weight is greater than 0
    # goodvis = wgt > 0
    # likeln = -0.5 * lnlike[goodvis].sum()
    likeln = -0.5 * lnlike.sum()
    # print(pcd, likeln)
    if likeln * 0 != 0:
        likeln = -numpy.inf

    return likeln, amp


def lnprob(
    pzero_regions,
    vis_complex,
    wgt,
    uuu,
    vvv,
    pcd,
    fixindx,
    paramSetup,
    computeamp=True,
    miriad=False,
):
    """

    Computes ln probabilities via ln prior + ln likelihood

    """

    lp, mu = lnprior(pzero_regions, paramSetup)

    if not numpy.isfinite(lp):
        probln = -numpy.inf
        mu = 1
        return probln, mu

    ll, mu = lnlike(
        pzero_regions,
        vis_complex,
        wgt,
        uuu,
        vvv,
        pcd,
        fixindx,
        paramSetup,
        computeamp=computeamp,
        miriad=miriad,
    )

    normalization = 1.0  # 2 * real.size
    probln = lp * normalization + ll
    #    print(probln, lp*normalization, ll)   # remove

    return probln, mu


#######################################


class AlarmException(Exception):
    pass


def alarmHandler(signum, frame):
    raise AlarmException


def nonBlockingRawInput(prompt="", timeout=20, response="yes"):
    """ """
    import signal

    signal.signal(signal.SIGALRM, alarmHandler)
    signal.alarm(timeout)
    try:
        text = input(prompt)
        signal.alarm(0)
        return text
    except AlarmException:
        print("\nPrompt timeout. Continuing...")
    signal.signal(signal.SIGALRM, signal.SIG_IGN)
    return response


def query_yes_no(question, default=None):
    """Ask a yes/no question via raw_input() and return their answer.

    "question" is a string that is presented to the user.
    "default" is the presumed answer if the user just hits <Enter>.
        It must be "yes" (the default), "no" or None (meaning
        an answer is required of the user).

    The "answer" return value is True for "yes" or False for "no".
    """

    import sys

    valid = {"yes": True, "y": True, "ye": True, "no": False, "n": False}
    if default is None:
        prompt = " [y/n] "
    elif default == "yes":
        prompt = " [Y/n] "
    elif default == "no":
        prompt = " [y/N] "
    else:
        raise ValueError("invalid default answer: '%s'" % default)

    while True:
        sys.stdout.write(question + prompt)
        choice = input().lower()
        if default is not None and choice == "":
            return valid[default]
        elif choice in valid:
            return valid[choice]
        else:
            sys.stdout.write("Please respond with 'yes' or 'no' " "(or 'y' or 'n').\n")


#        sys.stdout.flush()


def email_self(msg, receiver="beyer@ph1.uni-koeln.de"):
    """
    Parameters
    ----------
    msg: str
        in email

    """

    import os

    # email
    SENDMAIL = "/usr/sbin/sendmail"
    p = os.popen("%s -t" % SENDMAIL, "w")
    p.write("To: " + receiver + "\n")
    p.write("Subject: uvmcmcfit needs a respond to continuue. \n")
    p.write("\n")  # blank line separating headers from body

    message = msg + "\n\n" + " Continue?"

    p.write(message)
    sts = p.close()
    if sts != 0:
        print("Sendmail exit status {}".format(sts))


def main():
    configloc = "config.yaml"
    configfile = open(configloc, "r")
    config = yaml.safe_load(configfile)

    # Determine if we are going to compute the amplification of every model
    if list(config.keys()).count("ComputeAmp") > 0:
        computeamp = config["ComputeAmp"]
    else:
        computeamp = True

    # Determine parallel processing options
    if list(config.keys()).count("MPI") > 0:
        mpi = config["MPI"]
    else:
        mpi = False

    # multiple processors on a cluster using MPI
    if mpi:
        from emcee.utils import MPIPool

        # One thread per slot
        Nthreads = 1

        # Initialize the pool object
        pool = MPIPool()

        # If this process is not running as master, wait for instructions, then exit
        if not pool.is_master():
            pool.wait()
            sys.exit(0)

    # Single processor with Nthreads cores
    else:
        if list(config.keys()).count("Nthreads") > 0:
            # set the number of threads to use for parallel processing
            Nthreads = config["Nthreads"]
        else:
            Nthreads = 1

        # Initialize the pool object
        # pool = ""

    # --------------------------------------------------------------------------
    # Read in ALMA image and beam
    # im = fits.getdata(config['ImageName'])
    # im = im[0, 0, :, :].copy()
    headim = fits.getheader(config["ImageName"])

    # get resolution in ALMA image
    # celldata = numpy.abs(headim['CDELT1'] * 3600)

    # --------------------------------------------------------------------------
    # read in visibility data
    visfile = config["UVData"]

    # Determine if we will use miriad to compute simulated visibilities
    if list(config.keys()).count("UseMiriad") > 0:
        miriad = config["UseMiriad"]

        if miriad:
            interactive = False
            index = visfile.index("uvfits")
            visfilemiriad = visfile[0:index] + "miriad"

            # scale the weights
            newvisfile = visfile[0:index] + "scaled.uvfits"
            uvutil.scalewt(visfile, newvisfile)
            visfile = newvisfile
        else:
            miriad = False
    else:
        miriad = False

    # attempt to process multiple visibility files.  This won't work if miriad=True
    try:
        filetype = visfile[-6:]
        if filetype == "uvfits":
            uvfits = True
        else:
            uvfits = False
        uuu, vvv, www = uvutil.uvload(visfile)
        pcd = uvutil.pcdload(visfile)
        vis_complex, wgt = uvutil.visload(visfile)
    except:
        try:
            for i, ivisfile in enumerate(visfile):
                filetype = ivisfile[-6:]
                if filetype == "uvfits":
                    uvfits = True
                else:
                    uvfits = False
                iuuu, ivvv, iwww = uvutil.uvload(ivisfile)
                ipcd = uvutil.pcdload(ivisfile)
                ivis_complex, iwgt = uvutil.visload(ivisfile)
                if i == 0:
                    uuu = iuuu
                    vvv = ivvv
                    pcd = ipcd
                    vis_complex = ivis_complex
                    wgt = iwgt
                else:
                    uuu = numpy.append(uuu, iuuu)
                    vvv = numpy.append(vvv, ivvv)
                    if ipcd != pcd:
                        data1 = visfile[0]
                        data2 = visfile[ivisfile]
                        msg = (
                            "Phase centers in "
                            + data1
                            + " and "
                            + data2
                            + " do not match.  Please ensure phase "
                            + "centers in all visibility datasets are equal."
                        )
                        print(msg)
                        raise TypeError
                    vis_complex = numpy.append(vis_complex, ivis_complex)
                    wgt = numpy.append(wgt, iwgt)
        except:
            msg = (
                "Visibility datasets must be specified as either a string or "
                "a list of strings."
            )
            print(msg)
            raise TypeError

    # remove the data points with zero or negative weight
    positive_definite = wgt > 0
    assert (
        len(positive_definite[positive_definite]) > 0
    ), " --- Find no data to fit, check the weights --- "
    vis_complex = vis_complex[positive_definite]
    wgt = wgt[positive_definite]
    uuu = uuu[positive_definite]
    vvv = vvv[positive_definite]
    # www = www[positive_definite]

    npos = wgt.size

    # ----------------------------------------------------------------------------
    # Load input parameters
    paramSetup = setuputil.loadParams(config)
    nwalkers = paramSetup["nwalkers"]
    nregions = paramSetup["nregions"]
    nparams = paramSetup["nparams"]
    pname = paramSetup["pname"]
    nsource_regions = paramSetup["nsource_regions"]

    # Use an intermediate posterior PDF to initialize the walkers if it exists
    posteriorloc = "posteriorpdf.fits"
    if os.path.exists(posteriorloc):
        # read the latest posterior PDFs
        print("Found existing posterior PDF file: {:s}".format(posteriorloc))
        posteriordat = Table.read(posteriorloc)
        if len(posteriordat) > 1:
            # assign values to pzero
            nlnprob = 1
            pzero = numpy.zeros((nwalkers, nparams))
            startindx = nlnprob
            for j in range(nparams):
                namej = posteriordat.colnames[j + startindx]
                pzero[:, j] = posteriordat[namej][-nwalkers:]

            # number of mu measurements
            nmu = len(posteriordat.colnames) - nparams - nlnprob

            # output name is based on most recent burnin file name
            realpdf = True
        else:
            realpdf = False
    else:
        realpdf = False

    if not realpdf:
        extendedpname = ["lnprob"]
        extendedpname.extend(pname)
        nmu = 0
        for regioni in range(nregions):
            ri = str(regioni)
            if paramSetup["nlens_regions"][regioni] > 0:
                nsource = nsource_regions[regioni]
                for i in range(nsource):
                    si = ".Source" + str(i) + ".Region" + ri
                    extendedpname.append("mu_tot" + si)
                    nmu += 1
                for i in range(nsource):
                    si = ".Source" + str(i) + ".Region" + ri
                    extendedpname.append("mu_aper" + si)
                    nmu += 1
                extendedpname.append("mu_tot.Region" + ri)
                extendedpname.append("mu_aper.Region" + ri)
                nmu += 2
        posteriordat = Table(names=extendedpname)
        pzero = numpy.array(paramSetup["pzero"])

    # make sure no parts of pzero exceed p_u or p_l
    # arrayp_u = numpy.array(p_u)
    # arrayp_l = numpy.array(p_l)
    # for j in range(nwalkers):
    #    exceed = arraypzero[j] >= arrayp_u
    #    arraypzero[j, exceed] = 2 * arrayp_u[exceed] - arraypzero[j, exceed]
    #    exceed = arraypzero[j] <= arrayp_l
    #    arraypzero[j, exceed] = 2 * arrayp_l[exceed] - arraypzero[j, exceed]
    # pzero = arraypzero
    # p_u = arrayp_u
    # p_l = arrayp_l

    # determine the indices for fixed parameters
    fixindx = setuputil.fixParams(paramSetup)
    fixindx = list(map(int, fixindx))

    # Initialize the sampler with the chosen specs.
    if mpi:
        sampler = emcee.EnsembleSampler(
            nwalkers,
            nparams,
            lnprob,
            pool=pool,
            args=[
                vis_complex,
                wgt,
                uuu,
                vvv,
                pcd,
                fixindx,
                paramSetup,
                computeamp,
                miriad,
            ],
        )
    else:
        sampler = emcee.EnsembleSampler(
            nwalkers,
            nparams,
            lnprob,
            args=[
                vis_complex,
                wgt,
                uuu,
                vvv,
                pcd,
                fixindx,
                paramSetup,
                computeamp,
                miriad,
            ],
            threads=Nthreads,
        )

    # Sample, outputting to a file
    # os.system('date')
    currenttime = time.time()

    # do burn-in if posteriorpdf.fits doesn't exist or contains any samples
    # But, it's difficult to judge how many steps is needed
    # need to may sure later that we are sampling longer than the AC time
    if not realpdf:
        burnin = 150
        print("*** Running Burn in phase of steps {:d} ***".format(burnin))
        try:
            pos0, lnprob0, rstate0 = sampler.run_mcmc(pzero, burnin)
            currenttime = time.time()
        except ValueError:
            pos0, lnprob0, rstate0, _ = sampler.run_mcmc(pzero, burnin)
        sampler.reset()  # reset chain

    else:
        pos0 = pzero

    import pickle as pickle

    # pos - A list of current positions of walkers in the parameter space; dim = (nwalkers, dim)
    # prob - The list of log posterior probabilities for the walkers at positions given by pos . The shape of this object is (nwalkers, dim).
    # state - the random number generator state
    # amp - metadata 'blobs' associated with the current positon

    # below for testing..
    # nsamples = 300
    # nsessions = 2

    # in general, we want many samples.
    # niter & nsesions dep. on nwalkers
    #
    ####################################################
    # The saveint number has to be an integer, so choose the nsamples and nwalkers so that niter /nsessions/3 is equal to an integer and larger than 1, otherwise it will not save.
    ####################################################
    nsamples = 1e6
    niter = int(round(nsamples // nwalkers))
    nsessions = 15  # was 10
    saveint = niter // nsessions // 3

    valid = {"yes": True, "y": True, "ye": True, "no": False, "n": False}

    for i in range(nsessions):
        saveidx = 0
        for pos, prob, state, amp in sampler.sample(
            pos0, iterations=int(niter // nsessions)
        ):
            # using sampler.sample() will have pre-defined 0s in elements (cf. run_mcmc())
            print(currenttime)
            walkers, steps, dim = sampler.chain.shape
            result = [
                "Mean Acceptance fraction across all walkers of this iteration: {:.2f}".format(
                    numpy.mean(sampler.acceptance_fraction)
                ),
                "Mean lnprob and Max lnprob values: {:f} {:f}".format(
                    numpy.mean(prob), numpy.max(prob)
                ),
                "Time to run previous set of walkers (seconds): {:f}".format(
                    time.time() - currenttime
                ),
            ]
            print("\n".join(result))
            f = open("summary.txt", "a")
            f.write("\n".join(result))
            f.write("\n")
            f.close()

            currenttime = time.time()
            # ff.write(str(prob))

            superpos = numpy.zeros(1 + nparams + nmu)
            for wi in range(nwalkers):
                superpos[0] = prob[wi]
                superpos[1 : nparams + 1] = pos[wi]
                superpos[nparams + 1 : nparams + nmu + 1] = amp[wi]
                posteriordat.add_row(superpos)

            # only save if it has went through every saveint iterations or is the last sample
            if not sampler.chain[
                :, numpy.any(sampler.chain[0, :, :] != 0, axis=1), :
            ].shape[1] % saveint or (
                sampler.chain[
                    :, numpy.any(sampler.chain[0, :, :] != 0, axis=1), :
                ].shape[1]
                == int(niter // nsessions)
            ):
                print(
                    "Ran {:d} iterations in this session. Saving data".format(
                        sampler.chain[
                            :, numpy.any(sampler.chain[0, :, :] != 0, axis=1), :
                        ].shape[1]
                    )
                )
                posteriordat.write("posteriorpdf.fits", overwrite=True)
                # posteriordat.write('posteriorpdf.txt', format='ascii')

                saveidx = sampler.chain[
                    :, numpy.any(sampler.chain[0, :, :] != 0, axis=1), :
                ].shape[1]

        message = "We have finished {:d} iterations with {:d} walkers. ".format(
            sampler.chain[:, numpy.any(sampler.chain[0, :, :] != 0, axis=1), :].shape[
                1
            ],
            nwalkers,
        )

        if i < nsessions - 1:
            email_self(message)
            print(message)
            ret = nonBlockingRawInput(
                "Shall we continuue with next session? (Y/N)", timeout=600
            ).lower()

            while not ret in valid:
                print("Please respond with 'yes' or 'no' (or 'y' or 'n').\n")
                ret = nonBlockingRawInput(
                    "Shall we continuue with next session? (Y/N)", timeout=600
                ).lower()
            if not valid[ret]:
                import sys

                sys.exit("Quiting... ")
                if mpi:
                    pool.close()

        sampler.reset()
        pos0 = pos

    f = open("summary.txt", "a")
    f.write("Finish all {:f} sessions \n".format(nsessions))
    f.write(
        "Total number of samples: {:f} \n".format(
            niter / nsessions * nsessions * nwalkers
        )
    )
    f.write("\n")
    f.close()

    if mpi:
        pool.close()


if __name__ == "__main__":
    main()
