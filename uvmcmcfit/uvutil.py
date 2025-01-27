"""
2014 January 31
Shane Bussmann

Varius utilities related to operations on uvfits data files.
"""


import numpy
from astropy.io import fits


def pcdload(visfile):
    checker = visfile.find("uvfits")
    if checker == -1:
        uvfits = False
    else:
        uvfits = True
    if uvfits:
        # uv fits format
        visdata = fits.open(visfile)
        visheader = visdata[0].header

        if visheader["NAXIS"] == 7:
            # identify the phase center
            try:
                pcd_ra = visdata["AIPS SU "].data["RAEPO"][0]
                pcd_dec = visdata["AIPS SU "].data["DECEPO"][0]
            except:
                pcd_ra = visheader["CRVAL6"]
                pcd_dec = visheader["CRVAL7"]
            if pcd_ra < 0:
                pcd_ra += 360
            pcd = [pcd_ra, pcd_dec]
            return pcd

        if visheader["NAXIS"] == 6:
            pcd_ra = visdata[0].header["CRVAL5"]
            pcd_dec = visdata[0].header["CRVAL6"]
            if pcd_ra < 0:
                pcd_ra += 360
            pcd = [pcd_ra, pcd_dec]
            return pcd

    else:
        # CASA MS
        pcd = MSpcd(visfile) 
        return pcd


def MSpcd(msFile):
    """
    Explore different ways to get phase center consistent with data that has been regridded from one epoch to another

    Parameters
    ----------
    msFile: string
        measurement set filename

    Returns
    -------
    pcd: list
        phase center

    Note
    ----
    which method gives the phase center that correspond to that used in CASA imaging needs further investigation
    """

    from casatools import table
    tb = table()

    tb.open(msFile + "/FIELD")
    old = tb.getcol("DELAY_DIR")  # , fieldid
    new = tb.getcol("PHASE_DIR")

    def shiftRA(ra):
        try:
            if len(ra) > 1:
                ra = [ra_i + numpy.pi * 2 for ra_i in numpy.squeeze(ra) if ra_i < 0.0]
            else:
                if ra < 0.0:
                    ra += numpy.pi * 2.0
        #                print(ra)
        except:
            if ra < 0.0:
                ra += numpy.pi * 2.0
        #            print(ra)
        return ra

    # if old.shape[-1] > 1:
    #     raise("Should check if the MS file truly only contains the science target!")

    if old.shape[-1] > 1:
        # for this Cloverleaf set, cloverleaf is the last source
        old_ra, old_dec = numpy.squeeze(old)[0][-1], numpy.squeeze(old)[-1][-1]
        old = numpy.array([[old_ra], [old_dec]])
        new_ra, new_dec = numpy.squeeze(new)[0][-1], numpy.squeeze(new)[-1][-1]
        new = numpy.array([[new_ra], [new_dec]])

    old[0] = shiftRA(old[0])
    new[0] = shiftRA(new[0])

    # old phase center
    # _pcd_ra, _pcd_dec = old[0] * 180. / numpy.pi, old[1] * 180. / numpy.pi

    # new phase center
    pcd_ra, pcd_dec = new[0] * 180.0 / numpy.pi, new[1] * 180.0 / numpy.pi
    # print(pcd_ra, pcd_dec)
    tb.close()
    try:
        pcd = pcd_ra[0][0], pcd_dec[0][0]
    except IndexError:
        pcd = pcd_ra[0], pcd_dec[0]
    return list(pcd)


def MSpcd2(msFile):
    """using CASA taskinit.ms #deprecated: Using now from casatools import ms

    Parameters
    ----------
    msFile: string
        measurement set filename

    Returns
    -------
    pcd:
        phase center

    Note
    ----
    may need further modification if nField > 1
    unless it is the same for all fields
    (need further investigation)
    """

    from casatools import ms

    ms.open(msFile)
    pc = ms.getfielddirmeas()
    if not isinstance(pc, dict) is True:
        pc()
    epoch = pc["refer"]
    pcd_dec = pc["m1"]["value"] * 180 / numpy.pi
    pcd_ra = pc["m0"]["value"] * 180 / numpy.pi
    if pcd_ra < 0:
        pcd_ra += 360
    ms.done()
    pcd = [pcd_ra, pcd_dec]
    return pcd


def oldMSpcd(msFile):
    """
    get phase center
    works for most data without uv-regrid

    Parameters
    ----------
    msFile: string
        measurement set filename

    Returns
    -------
    pcd:
        phase center

    Note
    ----
    by Shane
    works for data without uv-regrid using CASA's fixvis()
    the following will give the old phase center otherwise
    """

    from casatools import table
    tb = table()

    tb.open(msFile + "/SOURCE")
    pcd_ra = tb.getcol("DIRECTION")[0][0] * 180 / numpy.pi
    if pcd_ra < 0:
        pcd_ra += 360
    pcd_dec = tb.getcol("DIRECTION")[1][0] * 180 / numpy.pi
    tb.close()
    pcd = [pcd_ra, pcd_dec]
    return pcd


def uvload(visfile):
    """load in visibilities from a uv-file

    Parameters
    ----------
    visfile: string
        visibility data filename, can be model or data

    Returns
    -------
    uu: numpy.array
        u visibilities
    vv: numpy.array
        v visibilities
    ww: numpy.array


    Note
    ----
    08-14-2015:
    Although a better solution to fix the array size mismatch problem that arises when calling `checkvis.uvmcmcfitVis` maybe something similar to the to-be-implemented function: uvmodel.add
        which looks for nspw to shape model_complex
    """

    checker = visfile.find("uvfits")
    if checker == -1:
        uvfits = False
    else:
        uvfits = True
    if uvfits:
        visdata = fits.open(visfile)
        visibilities = visdata[0].data
        visheader = visdata[0].header

        if visheader["NAXIS"] == 7:
            # identify the channel frequency(ies):
            visfreq = visdata[1].data
            freq0 = visheader["CRVAL4"]
            dfreq = visheader["CDELT4"]
            cfreq = visheader["CRPIX4"]
            nvis = visibilities["DATA"][:, 0, 0, 0, 0, 0, 0].size
            nspw = visibilities["DATA"][0, 0, 0, :, 0, 0, 0].size
            nfreq = visibilities["DATA"][0, 0, 0, 0, :, 0, 0].size
            npol = visibilities["DATA"][0, 0, 0, 0, 0, :, 0].size
            if nfreq > 1:
                uu = numpy.zeros([nvis, nspw, nfreq, npol])
                vv = numpy.zeros([nvis, nspw, nfreq, npol])
                ww = numpy.zeros([nvis, nspw, nfreq, npol])
            else:
                # if miriad is True:
                #     uu = numpy.zeros([nvis, nspw, nfreq, npol])
                #     vv = numpy.zeros([nvis, nspw, nfreq, npol])
                #     ww = numpy.zeros([nvis, nspw, nfreq, npol])
                # else:
                #     uu = numpy.zeros([nvis, nspw, npol])
                #     vv = numpy.zeros([nvis, nspw, npol])
                #     ww = numpy.zeros([nvis, nspw, npol])
                uu = numpy.zeros([nvis, nspw, npol])
                vv = numpy.zeros([nvis, nspw, npol])
                ww = numpy.zeros([nvis, nspw, npol])

            # wgt = numpy.zeros([nvis, nspw, nfreq, npol])

            # get spw frequencies
            # reference freq + offset
            for ispw in range(nspw):
                if nspw > 1:
                    freqif = freq0 + visfreq["IF FREQ"][0][ispw]
                else:
                    try:
                        freqif = freq0 + visfreq["IF FREQ"][0]
                    except:
                        freqif = freq0
                # uu[:, ispw] = freqif * visibilities['UU']
                # vv[:, ispw] = freqif * visibilities['VV']

                for ipol in range(npol):
                    # then compute the spatial frequencies:
                    if nfreq > 1:
                        freq = (numpy.arange(nfreq) - cfreq + 1) * dfreq + freqif
                        freqvis = numpy.meshgrid(freq, visibilities["UU"])
                        uu[:, ispw, :, ipol] = freqvis[0] * freqvis[1]
                        freqvis = numpy.meshgrid(freq, visibilities["VV"])
                        vv[:, ispw, :, ipol] = freqvis[0] * freqvis[1]
                        freqvis = numpy.meshgrid(freq, visibilities["WW"])
                        ww[:, ispw, :, ipol] = freqvis[0] * freqvis[1]
                    else:
                        # if miriad is True:
                        #     freq = (numpy.arange(nfreq) - cfreq + 1) * dfreq + freqif
                        #     freqvis = numpy.meshgrid(freq, visibilities['UU'])
                        #     uu[:, ispw, :, ipol] = freqvis[0] * freqvis[1]
                        #     freqvis = numpy.meshgrid(freq, visibilities['VV'])
                        #     vv[:, ispw, :, ipol] = freqvis[0] * freqvis[1]
                        #     freqvis = numpy.meshgrid(freq, visibilities['WW'])
                        #     ww[:, ispw, :, ipol] = freqvis[0] * freqvis[1]
                        # else:
                        #     uu[:, ispw, ipol] = freqif * visibilities['UU']
                        #     vv[:, ispw, ipol] = freqif * visibilities['VV']
                        #     ww[:, ispw, ipol] = freqif * visibilities['WW']
                        uu[:, ispw, ipol] = freqif * visibilities["UU"]
                        vv[:, ispw, ipol] = freqif * visibilities["VV"]
                        ww[:, ispw, ipol] = freqif * visibilities["WW"]

        if visheader["NAXIS"] == 6:
            # identify the channel frequency(ies):
            freq0 = visheader["CRVAL4"]
            dfreq = visheader["CDELT4"]
            cfreq = visheader["CRPIX4"]
            nvis = visibilities["DATA"][:, 0, 0, 0, 0, 0].size
            nfreq = visibilities["DATA"][0, 0, 0, :, 0, 0].size
            npol = visibilities["DATA"][0, 0, 0, 0, :, 0].size
            if nfreq > 1:
                uu = numpy.zeros([nvis, nfreq, npol])
                vv = numpy.zeros([nvis, nfreq, npol])
                ww = numpy.zeros([nvis, nfreq, npol])
            else:
                uu = numpy.zeros([nvis, npol])
                vv = numpy.zeros([nvis, npol])
                ww = numpy.zeros([nvis, npol])
            # wgt = numpy.zeros([nvis, nspw, nfreq, npol])

            freqif = freq0
            # uu[:, ispw] = freqif * visibilities['UU']
            # vv[:, ispw] = freqif * visibilities['VV']

            for ipol in range(npol):
                # then compute the spatial frequencies:
                if nfreq > 1:
                    freq = (numpy.arange(nfreq) - cfreq + 1) * dfreq + freqif
                    freqvis = numpy.meshgrid(freq, visibilities["UU"])
                    uu[:, 0, :, ipol] = freqvis[0] * freqvis[1]
                    freqvis = numpy.meshgrid(freq, visibilities["VV"])
                    vv[:, 0, :, ipol] = freqvis[0] * freqvis[1]
                    freqvis = numpy.meshgrid(freq, visibilities["WW"])
                    ww[:, 0, :, ipol] = freqvis[0] * freqvis[1]
                else:
                    uu[:, ipol] = freqif * visibilities["UU"]
                    vv[:, ipol] = freqif * visibilities["VV"]
                    ww[:, ipol] = freqif * visibilities["WW"]

    else:
        from casatools import table 
        tb = table()
	
        # read in the uvfits data
        tb.open(visfile)
        uvw = tb.getcol("UVW")
        uvspw = tb.getcol("DATA_DESC_ID")
        tb.close()

        tb.open(visfile + "/SPECTRAL_WINDOW")
        freq = tb.getcol("CHAN_FREQ")
        tb.close()

        tb.open(visfile + "/POLARIZATION")
        polinfo = tb.getcol("NUM_CORR")
        tb.close()
        npol = polinfo[0]

        nspw = len(freq[0])

        for ispw in range(nspw):
            ilam = 3e8 / freq[0][ispw] 
            indx_spw = uvspw == ispw
            uvw[:, indx_spw] /= ilam

        uu = []
        vv = []
        ww = []
        for ipol in range(npol):
            uu.append(uvw[0, :])
            vv.append(uvw[1, :])
            ww.append(uvw[2, :])
            
        uu = numpy.array(uu)
        vv = numpy.array(vv)
        ww = numpy.array(ww)
        
        if uu[:, 0].size == 1:
            uu = uu.flatten()
            vv = vv.flatten()
            ww = ww.flatten()
            
        tb.open(visfile)
        vis_weight = tb.getcol("WEIGHT")
        tb.close()
            
        data_wgt = vis_weight        #added this part from Visload: Problem is that there is an index mismatch from uuu[positive_definite], where positive_definite has shape (2,1,nrow) and uuu has (2,nrow) and needs the place holder for the middle index.
        wgtshape = data_wgt.shape #
        if len(wgtshape) == 2: #
            npol = wgtshape[0] #
            nrow = wgtshape[1] #
            wgtshape = (npol, 1, nrow) #

        uu = uu.reshape(wgtshape) #
        vv = vv.reshape(wgtshape) #
        ww = ww.reshape(wgtshape) #
        
    return uu, vv, ww


def visload(visfile):
    checker = visfile.find("uvfits")
    if checker == -1:
        uvfits = False
    else:
        uvfits = True
    if uvfits:
        visdata = fits.open(visfile)
        # get the telescope name
        visheader = visdata[0].header
        # telescop = visheader['TELESCOP']

        # if we are dealing with SMA data
        if visheader["NAXIS"] == 6:
            nfreq = visdata[0].data["DATA"][0, 0, 0, :, 0, 0].size
            if nfreq > 1:
                data_real = visdata[0].data["DATA"][:, 0, 0, :, :, 0]
                data_imag = visdata[0].data["DATA"][:, 0, 0, :, :, 1]
                data_wgt = visdata[0].data["DATA"][:, 0, 0, :, :, 2]
            else:
                data_real = visdata[0].data["DATA"][:, 0, 0, 0, :, 0]
                data_imag = visdata[0].data["DATA"][:, 0, 0, 0, :, 1]
                data_wgt = visdata[0].data["DATA"][:, 0, 0, 0, :, 2]

        # if we are dealing with ALMA or PdBI data
        if visheader["NAXIS"] == 7:
            nfreq = visdata[0].data["DATA"][0, 0, 0, 0, :, 0, 0].size
            if nfreq > 1:
                data_real = visdata[0].data["DATA"][:, 0, 0, :, :, :, 0]
                data_imag = visdata[0].data["DATA"][:, 0, 0, :, :, :, 1]
                data_wgt = visdata[0].data["DATA"][:, 0, 0, :, :, :, 2]
            else:
                data_real = visdata[0].data["DATA"][:, 0, 0, :, 0, :, 0]
                data_imag = visdata[0].data["DATA"][:, 0, 0, :, 0, :, 1]
                data_wgt = visdata[0].data["DATA"][:, 0, 0, :, 0, :, 2]

        data_complex = numpy.array(data_real) + 1j * numpy.array(data_imag)

    else:
        from casatools import table
        tb = table()

        # read in the CASA MS
        tb.open(visfile)
        vis_complex = tb.getcol("DATA")
        vis_weight = tb.getcol("WEIGHT")
        tb.close()

        # tb.open(visfile + '/POLARIZATION')
        # polinfo = tb.getcol('NUM_CORR')
        # npol = polinfo[0]

        data_complex = vis_complex
        data_wgt = vis_weight
        wgtshape = data_wgt.shape
        if len(wgtshape) == 2:
            npol = wgtshape[0]
            nrow = wgtshape[1]
            wgtshape = (npol, 1, nrow)

        data_wgt = data_wgt.reshape(wgtshape) 
        # data_complex = []
        # data_wgt = []
        # for ipol in range(npol):
        #    data_complex.append(vis_complex[ipol, 0, :])
        #    data_wgt.append(vis_weight[ipol, :])
        # data_complex = numpy.array(data_complex)
        # data_wgt = numpy.array(data_wgt)

    return data_complex, data_wgt


def getStatWgt(real_raw, imag_raw, wgt_raw):
    """
    Compute the weights as the rms scatter in the real and imaginary
    visibilities.
    """

    nvis = real_raw[:, 0].size
    freqsize = real_raw[0, :].size
    wgt_scaled = numpy.zeros([nvis, freqsize])
    for i in range(nvis):
        gwgt = wgt_raw[i, :] > 0
        ngwgt = wgt_raw[i, gwgt].size
        if ngwgt > 2:
            reali = real_raw[i, gwgt]
            imagi = imag_raw[i, gwgt]
            rms_real = numpy.std(reali)
            rms_imag = numpy.std(imagi)
            rms_avg = (rms_real + rms_imag) / 2.0
            wgt_scaled[i, :] = 1 / rms_avg**2
    return wgt_scaled


def statwt(visfileloc, newvisfileloc, ExcludeChannels=False):
    """
    Replace the weights in 'visfile' with weights computed via getStatWgt.
    """

    visfile = fits.open(visfileloc)
    data_complex, data_wgt = visload(visfileloc)
    data_real = numpy.real(data_complex)
    data_imag = numpy.imag(data_complex)
    wgt_original = data_wgt.copy()

    if ExcludeChannels:
        nwindows = len(ExcludeChannels) // 2
        for win in range(0, nwindows * 2, 2):
            chan1 = ExcludeChannels[win]
            chan2 = ExcludeChannels[win + 1]
            if data_real.ndim == 4:
                data_wgt[:, :, chan1:chan2, :] = 0
            else:
                data_wgt[:, chan1:chan2, :] = 0

    # get the number of visibilities, spws, frequencies, polarizations
    if data_real.ndim == 4:
        nvis = data_real[:, 0, 0, 0].size
        nspw = data_real[0, :, 0, 0].size
        nfreq = data_real[0, 0, :, 0].size
        npol = data_real[0, 0, 0, :].size
        wgt = numpy.zeros([nvis, nspw, nfreq, npol])
    if data_real.ndim == 3:
        # no spw
        nvis = data_real[:, 0, 0].size
        nspw = 0
        nfreq = data_real[0, :, 0].size
        npol = data_real[0, 0, :].size
        wgt = numpy.zeros([nvis, nfreq, npol])

    if nspw > 0:
        for ispw in range(nspw):
            for ipol in range(npol):
                # compute real and imaginary components of the visibilities
                real_raw = data_real[:, ispw, :, ipol]
                imag_raw = data_imag[:, ispw, :, ipol]
                wgt_raw = data_wgt[:, ispw, :, ipol]

                wgt_orig = wgt_original[:, ispw, :, ipol]
                oktoreplace = wgt_orig > 0

                wgt_scaled = getStatWgt(real_raw, imag_raw, wgt_raw)
                wgt_temp = wgt[:, ispw, :, ipol]
                wgt_temp[oktoreplace] = wgt_scaled[oktoreplace]
                wgt[:, ispw, :, ipol] = wgt_temp

        visfile[0].data["DATA"][:, 0, 0, :, :, :, 2] = wgt

    else:
        for ipol in range(npol):
            # compute real and imaginary components of the visibilities
            real_raw = data_real[:, :, ipol]
            imag_raw = data_imag[:, :, ipol]
            wgt_raw = data_wgt[:, :, ipol]

            wgt_scaled = getStatWgt(real_raw, imag_raw, wgt_raw)
            wgt[:, :, ipol] = wgt_scaled

        # visfile[0].data['DATA'][:, 0, 0, :, :, 2] = wgt

        import pdb

        pdb.set_trace()
        if nfreq > 1:
            try:
                visfile[0].data["DATA"][:, 0, 0, :, :, :, 2] = wgt
            except ValueError:
                # wgt.ndim is 3 if data_real.dim is 3
                if data_real.ndim == 3:
                    # wgt.shape is defined using nvis, nfreq, npol
                    visfile[0].data["DATA"][:, 0, 0, 0, :, :, 2] = wgt
        elif nfreq == 1:
            visfile[0].data["DATA"][:, 0, 0, :, 0, :, 2] = wgt

    visfile.writeto(newvisfileloc)

    return


def scalewt(visdataloc, newvisdataloc):
    """
    scale the weights such that:
    Sum(wgt * real^2 + wgt * imag^2) = N_visibilities

    Parameters
    ----------
    visdataloc: string
        uv-data filename

    Returns
    -------
    newvisdataloc: string
        output scaled uv-data filename

    """

    visfile = fits.open(visdataloc)
    data_complex, data_wgt = visload(visdataloc)
    data_real = numpy.real(data_complex)
    data_imag = numpy.imag(data_complex)

    # scale the weights such that:
    # Sum(wgt * real^2 + wgt * imag^2) = N_visibilities
    wgt_scaled = data_wgt
    wgzero = wgt_scaled > 0
    N_vis = 2 * wgt_scaled[wgzero].size
    wgtrealimag = wgt_scaled * (data_real**2 + data_imag**2)
    wgtsum = wgtrealimag[wgzero].sum()
    wgtscale = N_vis / wgtsum
    print("Scaling the weights by a factor of ", wgtscale)
    wgt_scaled = wgt_scaled * wgtscale

    # read in the uvfits data
    if data_real.ndim == 4:
        visfile[0].data["DATA"][:, 0, 0, :, :, :, 2] = wgt_scaled
    else:
        if visfile[0].header["NAXIS"] == 6:
            nfreq = visfile[0].data["DATA"][0, 0, 0, :, 0, 0].size
            if nfreq > 1:
                visfile[0].data["DATA"][:, 0, 0, :, :, 2] = wgt_scaled
            else:
                visfile[0].data["DATA"][:, 0, 0, 0, :, 2] = wgt_scaled
        if visfile[0].header["NAXIS"] == 7:
            visfile[0].data["DATA"][:, 0, 0, 0, :, :, 2] = wgt_scaled
    visfile.writeto(newvisdataloc, clobber=True)


def zerowt(visdataloc, newvisdataloc, ExcludeChannels):
    visfile = fits.open(visdataloc)
    data_real, data_imag, data_wgt = visload(visfile)
    nwindows = len(ExcludeChannels) / 2
    for win in range(0, nwindows * 2, 2):
        chan1 = ExcludeChannels[win]
        chan2 = ExcludeChannels[win + 1]
        if data_real.ndim == 4:
            visfile[0].data["DATA"][:, 0, 0, :, chan1:chan2, :, 2] = 0.0
        else:
            visfile[0].data["DATA"][:, 0, 0, chan1:chan2, :, 2] = 0.0
    visfile.writeto(newvisdataloc)


# AS OF 2014-02-24, spectralavg IS NON-FUNCTIONAL


def spectralavg(visdataloc, newvisdataloc, Nchannels):
    # bin in frequency space to user's desired spectral resolution
    vis_data = fits.open(visdataloc)
    data_real, data_imag, data_wgt = visload(vis_data)

    # get the number of visibilities, spws, frequencies, polarizations
    if data_real.ndim == 4:
        nvis = data_real[:, 0, 0, 0].size
        nspw = data_real[0, :, 0, 0].size
        nchan = data_real[0, 0, :, 0].size
        npol = data_real[0, 0, 0, :].size
        real_bin = numpy.zeros([nvis, nspw, Nchannels, npol])
        imag_bin = numpy.zeros([nvis, nspw, Nchannels, npol])
        wgt_bin = numpy.zeros([nvis, nspw, Nchannels, npol])
    if data_real.ndim == 3:
        nvis = data_real[:, 0, 0].size
        nspw = 0
        nchan = data_real[0, :, 0].size
        npol = data_real[0, 0, :].size
        real_bin = numpy.zeros([nvis, Nchannels, npol])
        imag_bin = numpy.zeros([nvis, Nchannels, npol])
        wgt_bin = numpy.zeros([nvis, Nchannels, npol])

    chan1 = 0
    dchan = nchan / Nchannels
    chan2 = chan1 + dchan
    if nspw > 1:
        for ispw in range(nspw):
            for ipol in range(npol):
                for ichan in range(Nchannels):
                    for i in range(nvis):
                        gwgt = data_wgt[i, ispw, chan1:chan2, ipol] > 0
                        ngwgt = data_wgt[i, ispw, gwgt, ipol].size
                        if ngwgt == 0:
                            continue
                        value = data_real[i, ispw, gwgt, ipol].sum() / ngwgt
                        real_bin[i, ispw, ichan, ipol] = value
                        value = data_imag[i, ispw, gwgt, ipol].sum() / ngwgt
                        imag_bin[i, ispw, ichan, ipol] = value
                        value = data_wgt[i, ispw, gwgt, ipol].mean() * ngwgt
                        wgt_bin[i, ispw, ichan, ipol] = value
                    chan1 = chan2
                    chan2 = chan1 + dchan

        newvis = numpy.zeros([nvis, 1, 1, nspw, Nchannels, npol, 3])
        newvis[:, 0, 0, :, :, :, 0] = real_bin
        newvis[:, 0, 0, :, :, :, 1] = imag_bin
        newvis[:, 0, 0, :, :, :, 2] = wgt_bin

        oldcrpix4 = vis_data[0].header["CRPIX4"]
        newcrpix4 = numpy.float(oldcrpix4) / nchan * Nchannels
        newcrpix4 = numpy.floor(newcrpix4) + 1
        vis_data[0].header["CRPIX4"] = newcrpix4

        oldcdelt4 = vis_data[0].header["CDELT4"]
        newcdelt4 = oldcdelt4 * nchan / Nchannels
        vis_data[0].header["CDELT4"] = newcdelt4
    else:
        for ipol in range(npol):
            for ichan in range(Nchannels):
                for i in range(nvis):
                    gwgt = data_wgt[i, chan1:chan2, ipol] > 0
                    ngwgt = data_wgt[i, gwgt, ipol].size
                    if ngwgt == 0:
                        continue
                    value = data_real[i, gwgt, ipol].sum() / ngwgt
                    real_bin[i, ichan, ipol] = value
                    value = data_imag[i, gwgt, ipol].sum() / ngwgt
                    imag_bin[i, ichan, ipol] = value
                    value = data_wgt[i, gwgt, ipol].mean() * ngwgt
                    wgt_bin[i, ichan, ipol] = value
                chan1 = chan2
                chan2 = chan1 + dchan

        newvis = numpy.zeros([nvis, 1, 1, Nchannels, npol, 3])
        newvis[:, 0, 0, :, :, 0] = real_bin
        newvis[:, 0, 0, :, :, 1] = imag_bin
        newvis[:, 0, 0, :, :, 2] = wgt_bin

        oldcrpix4 = vis_data[0].header["CRPIX4"]
        newcrpix4 = numpy.float(oldcrpix4) / nchan * Nchannels
        newcrpix4 = numpy.floor(newcrpix4) + 1
        vis_data[0].header["CRPIX4"] = newcrpix4
        vis_data[0].header["NAXIS4"] = Nchannels

        oldcdelt4 = vis_data[0].header["CDELT4"]
        newcdelt4 = oldcdelt4 * nchan / Nchannels
        vis_data[0].header["CDELT4"] = newcdelt4
    vis_data[0].data["DATA"][:] = newvis
    vis_data.writeto(newvisdataloc)


def getFreqfromtb(msFile):
    """get a list of IF from the measurement set table
    same as ('CHAN_FREQ') if

    Parameters
    ----------
    msFile: string
      CASA measurement set

    Returns
    -------
    freq0: numpy.ndarray
        reference frequency in Hz
    """

    from casatools import table
    tb = table()
    

    tb.open(msFile + "/SPECTRAL_WINDOW")
    freq0 = tb.getcol("REF_FREQUENCY")
    nchan = tb.getcol("NUM_CHAN")
    tb.close()
    return freq0


def checkMS(msFile, cont=True): 
    """get the number of spw and channels in the measurement set
    for continuum: combine into 1 channel and spw before running uvmcmcfit

    Parameters
    ----------
    msFile: string
        CASA measurement set

    Returns
    -------
    nspw:
        number of spw
    nchan:
        number of channels

    """
    from casatools import msmetadata #fixed to be usable again. Used import ms before which lead to erros. 
    msmd = msmetadata()
    msmd.open(msFile)
    nspw = msmd.nspw()
    for i in range(nspw): 
        # number of channels in each spw
        nchan = msmd.nchan(i)
    msmd.done()
    return nspw, nchan
