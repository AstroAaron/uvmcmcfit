"""

Inspect a model with specific parameters defined in config_sandbox.py

"""


import os
from subprocess import call

import numpy
import yaml
from astropy.io import fits

from . import lensutil, sample_vis, setuputil, uvutil, visualutil


def plot(
    cleanup=True,
    configloc="sandbox.yaml",
    interactive=True,
    threshold=1.2,
    plot=True,
    tag="sandbox",
):
    """

    Parameters
    ----------
    threshold: float
        in mJy, cleaning threshold

    """

    # read the input parameters
    configfile = open(configloc, "r")
    config = yaml.safe_load(configfile)

    paramSetup = setuputil.loadParams(config)
    fixindx = setuputil.fixParams(paramSetup)
    testfit = paramSetup["p_l"]
    visfile = config["UVData"]
    uuu, vvv, www = uvutil.uvload(visfile)
    pcd = uvutil.pcdload(visfile)
    vis_complex, wgt = uvutil.visload(visfile)

    # remove the data points with zero or negative weight
    positive_definite = wgt > 0
    vis_complex = vis_complex[positive_definite]
    wgt = wgt[positive_definite]
    uuu = uuu[positive_definite]
    vvv = vvv[positive_definite]

    testlnprob, testmu = lnprob(
        testfit, vis_complex, wgt, uuu, vvv, pcd, fixindx, paramSetup, computeamp=True
    )
    # prepend 1 dummy value to represent lnprob
    testfit = numpy.append(testlnprob, testfit)
    # append nmu dummy values that represent the magnification factors
    nlensedsource = paramSetup["nlensedsource"]
    nlensedregions = paramSetup["nlensedregions"]
    nmu = 2 * (numpy.array(nlensedsource).sum() + nlensedregions)

    for i in range(nmu):
        testfit = numpy.append(testfit, 0)

    print("lnprob: %f" % testlnprob)
    print("Using the following model parameters:")
    for k, v in zip(paramSetup["pname"], testfit[1:-4]):
        print("%s : %.4f" % (k, v))
    if plot:
        visualutil.plotFit(
            config,
            testfit,
            threshold,
            tag=tag,
            cleanup=cleanup,
            interactive=interactive,
        )
    return testlnprob


def lnprior(pzero_regions, paramSetup):
    """

    Function that computes the ln prior probabilities of the model parameters.

    """
    priorln = 0.0
    mu = 1

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
            # priorln += numpy.log(1.0/numpy.abs(p_l_regions - p_u_regions)).sum()
            priorln = 0.0
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
    fixed = (numpy.where(fixindx >= 0))[0]
    nfixed = fixindx[fixed].size
    p_u_regions = paramSetup["p_u"]
    poff_regions = p_u_regions.copy()
    poff_regions[:] = 0.0
    # for ifix in range(nfixed):
    #    poff_regions[fixed[ifix]] = pzero_regions[fixindx[fixed[ifix]]]
    for ifix in range(nfixed):
        ifixed = fixed[ifix]
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
        #        print(vis_complex.shape, model_complex.shape)     # remove
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
    pzero_regions, vis_complex, wgt, uuu, vvv, pcd, fixindx, paramSetup, computeamp=True
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
    )

    normalization = 1.0  # 2 * real.size
    probln = lp * normalization + ll

    return probln, mu
