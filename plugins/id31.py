#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import with_statement, print_function

"""Data Analysis plugin tailored for ID31

* integrate_simple: simple demo of a  
* integrate: a more advances 
 
"""

__authors__ = ["Jérôme Kieffer"]
__contact__ = "Jerome.Kieffer@ESRF.eu"
__license__ = "MIT"
__copyright__ = "European Synchrotron Radiation Facility, Grenoble, France"
__date__ = "04/04/2016"
__status__ = "development"
version = "0.1.0"

import os
import numpy
from dahu.plugin import Plugin, plugin_from_function
from dahu.factory import register
from threading import Semaphore
import logging
logger = logging.getLogger("plugin.pyFAI")

try:
    import pyFAI
except ImportError:
    logger.error("Failed to import PyFAI: download and install it from pypi")
try:
    import fabio
except ImportError:
    logger.error("Failed to import Fabio: download and install it from pypi")


def integrate_simple(poni_file, image_file, curve_file, nbins=1000):
    """Simple azimuthal integration for a single frame (very inefficient)
    
    :param poni_file: configuration of the geometry
    :param image_file: 
    :param curve_file: output file
    :param nbins: number of output bins
    """
    ai = pyFAI.load(poni_file)
    img = fabio.open(image_file).data
    ai.integrate1d(img, nbins, filename=curve_file, unit="2th_deg", method="splitpixel")
    return {"out_file": curve_file}
plugin_from_function(integrate_simple)


################################################
#  Class Cache for storing the data in a Borg  #
################################################


class DataCache(dict):
    """
    this class is a Borg : always returns the same values regardless to the instance of the object
    it is used as data storage for images ... with a limit on the number of images to keep in memory.
    """
    __shared_state = {}
    __data_initialized = False

    def __init__(self, max_size=10):
        """
        Constructor of DataCache
        @param max_size: number of element to keep in memory
        """
        self.__dict__ = self.__shared_state
        if DataCache.__data_initialized is False:
            DataCache.__data_initialized = True
            logger.debug("DataCache.__init__: initalization of the Borg")
            self.ordered = []
            self.dict = {}
            self.max_size = max_size
            self._sem = Semaphore()

    def __repr__(self):
        """
        """
        out = ["{"]
        for key in self.ordered:
            out.append(" '%s': %s," % (key, self.dict[key]))
        out.append("}")
        return os.linesep.join(out)

    def __setitem__(self, key, value):
        """
        x.__setitem__(i, y) <==> x[i]=y
        """
        with self._sem:
            logger.debug("DataCache.__setitem__: %s" % key)
            self.dict[key] = value
            if key in self.ordered:
                index = self.ordered.index(key)
                self.ordered.pop(index)
            if len(self.ordered) > self.max_size:
                firstKey = self.ordered.pop(0)
                logger.debug("Removing from cache: %s" % firstKey)
                self.dict.pop(firstKey)
            self.ordered.append(key)

    def __getitem__(self, key):
        """
        x.__getitem__(y) <==> x[y]
        """
        with self._sem:
            logger.debug("DataCache.__setitem__: %s" % key)
            index = self.ordered.index(key)
            self.ordered.pop(index)
            self.ordered.append(key)
            return self.dict[key]

    def __contains__(self, key):
        """
        D.__contains__(k) -> True if D has a key k, else False
        """
        return key in self.dict
    has_key = __contains__

    def __len__(self):
        """
        Returns the length of the object
        """
        return len(self.ordered)

    def get(self, key, default=None):
        """
        get method with default answer implemented
        """
        if key in self.ordered:
            return self.__getitem__(key)
        elif default is not None:
            self.__setitem__(key, default)
            return default

    def keys(self):
        """
        Returns the list of keys, ordered
        """
        logger.debug("DataCache.keys")
        return self.ordered[:]

    def pop(self, key):
        """
        Remove a key for the dictionary and return it's value
        """
        with self._sem:
            logger.debug("DataCache.pop %s" % key)
            try:
                index = self.ordered.index(key)
            except:
                raise KeyError
            self.ordered.pop(index)
            myData = self.dict.pop(key)
        return myData


# Use thre register decorator to make it available from Dahu
@register
class Integrate(Plugin):
    """This is the basic plugin of PyFAI for azimuthal integration
    
    Input parameters:
    :param poni_file: configuration of the geometry
    :param input_files:
    :param 
    
    Typical JSON file:
    {"poni_file": "/tmp/example.poni",
     "input_files": ["/tmp/file1.edf", "/tmp/file2.edf"],
     "monitor_values": [1, 1.1],
     "npt": 2000,
     "unit": "2th_deg",
    }
    """
    _ais = DataCache()  # key: str(a), value= ai

    def __init__(self):
        """
        """
        Plugin.__init__(self)
        self.ai = None  # this is the azimuthal integrator to use
        self.dest_dir = None
        self.ntp = 1000
        self.input_files = []
        self.monitor_values = None
        self.method = "full_ocl_csr"
        self.unit = "q_nm^-1"
        self.output_files = []

    def setup(self, kwargs):
        logger.debug("Integrate.setup")
        Plugin.setup(self, kwargs)

        if "output_dir" not in self.input:
            self.log_error("output_dir not in input")
        self.dest_dir = os.path.abspath(self.input["output_dir"])

        ponifile = self.input.get("poni_file", "")
        if not os.path.exists(ponifile):
            self.log_error("Ponifile: %s does not exist" % ponifile, do_raise=True)
        ai = pyFAI.load(ponifile)
        stored = self._ais.get(str(ai), ai)
        if stored is ai:
            self.ai = stored
        else:
            self.ai = stored.__deepcopy__()

        self.npt = int(self.input.get("npt", self.npt))
        self.unit = self.input.get("unit", self.unit)

    def process(self):
        Plugin.process(self)
        logger.debug("Integrate.process")
        if self.monitor_values is None:
            self.monitor_values = [1] * len(self.input_files)
        for monitor, fname in zip(self.monitor_values, self.input_files):
            if not os.path.exists(fname):
                self.log_error("image file: %s does not exist, skipping" % fname,
                               do_raise=False)
                continue
            if not monitor:
                self.log_error("Monitor value is %s: skipping image %s" % (monitor, fname),
                               do_raise=False)
                continue

            basename = os.path.splitext(os.path.basename(fname))[0]
            destination = os.path.join(self.dest_dir, basename + ".dat")
            data = fabio.open(fname).data
            self.ai.integrate1d(data, npt=self.npt, method=self.method,
                                safe=False,
                                filename=destination,
                                normalization_factor=monitor,
                                unit=self.unit)
            self.output_files.append(destination)

    def teardown(self):
        Plugin.teardown(self)
        logger.debug("Integrate.teardown")
        # Create some output data
        self.output["output_files"] = self.output_files

