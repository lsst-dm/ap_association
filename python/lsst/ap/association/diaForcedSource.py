# This file is part of ap_association.
#
# Developed for the LSST Data Management System.
# This product includes software developed by the LSST Project
# (https://www.lsst.org).
# See the COPYRIGHT file at the top-level directory of this distribution
# for details of code ownership.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Methods for force photometering direct and difference images at DiaObject
locations.
"""

__all__ = ["DiaForcedSourceTask", "DiaForcedSourcedConfig"]

import numpy as np

import lsst.afw.table as afwTable
from lsst.daf.base import DateTime
import lsst.geom as geom
from lsst.meas.base.pluginRegistry import register
from lsst.meas.base import (
    ForcedMeasurementTask,
    ForcedTransformedCentroidConfig,
    ForcedTransformedCentroidPlugin)
import lsst.pex.config as pexConfig
import lsst.pipe.base as pipeBase


class ForcedTransformedCentroidFromCoordConfig(ForcedTransformedCentroidConfig):
    """Configuration for the forced transformed coord algorithm.
    """
    pass


@register("ap_assoc_TransformedCentroid")
class ForcedTransformedCentroidFromCoordPlugin(ForcedTransformedCentroidPlugin):
    """Record the transformation of the reference catalog coord.
    The coord recorded in the reference catalog is tranformed to the
    measurement coordinate system and stored.

    Parameters
    ----------
    config : `ForcedTransformedCentroidFromCoordConfig`
        Plugin configuration
    name : `str`
        Plugin name
    schemaMapper : `lsst.afw.table.SchemaMapper`
        A mapping from reference catalog fields to output
        catalog fields. Output fields are added to the output schema.
    metadata : `lsst.daf.base.PropertySet`
        Plugin metadata that will be attached to the output catalog.

    Notes
    -----
    This can be used as the slot centroid in forced measurement when only a
    reference coord exits, allowing subsequent measurements to simply refer to
    the slot value just as they would in single-frame measurement.
    """

    ConfigClass = ForcedTransformedCentroidFromCoordConfig

    def measure(self, measRecord, exposure, refRecord, refWcs):
        targetWcs = exposure.getWcs()

        targetPos = targetWcs.skyToPixel(refRecord.getCoord())
        measRecord.set(self.centroidKey, targetPos)

        if self.flagKey is not None:
            measRecord.set(self.flagKey, refRecord.getCentroidFlag())


class DiaForcedSourcedConfig(pexConfig.Config):
    """Configuration for the generic DiaForcedSourcedTask class.
    """
    forcedMeasurement = pexConfig.ConfigurableField(
        target=ForcedMeasurementTask,
        doc="Subtask to force photometer DiaObjects in the direct and "
            "difference images.",
    )
    dropColumns = pexConfig.ListField(
        dtype=str,
        doc="Columns produced in forced measurement that can be dropped upon "
            "creation and storage of the final pandas data.",
    )

    def setDefaults(self):
        self.forcedMeasurement.plugins = ["ap_assoc_TransformedCentroid",
                                          "base_PsfFlux"]
        self.forcedMeasurement.doReplaceWithNoise = False
        self.forcedMeasurement.copyColumns = {
            "id": "diaObjectId",
            "coord_ra": "coord_ra",
            "coord_dec": "coord_dec"}
        self.forcedMeasurement.slots.centroid = "ap_assoc_TransformedCentroid"
        self.forcedMeasurement.slots.psfFlux = "base_PsfFlux"
        self.forcedMeasurement.slots.shape = None
        self.dropColumns = ['coord_ra', 'coord_dec', 'parent',
                            'ap_assoc_TransformedCentroid_x',
                            'ap_assoc_TransformedCentroid_y',
                            'base_PsfFlux_instFlux',
                            'base_PsfFlux_instFluxErr', 'base_PsfFlux_area',
                            'slot_PsfFlux_area', 'base_PsfFlux_flag',
                            'slot_PsfFlux_flag',
                            'base_PsfFlux_flag_noGoodPixels',
                            'slot_PsfFlux_flag_noGoodPixels',
                            'base_PsfFlux_flag_edge', 'slot_PsfFlux_flag_edge']


class DiaForcedSourceTask(pipeBase.Task):
    """Task for measuring and storing forced sources at DiaObject locations
    in both difference and direct images.
    """
    ConfigClass = DiaForcedSourcedConfig
    _DefaultName = "diaForcedSource"

    def __init__(self, **kwargs):
        pipeBase.Task.__init__(self, **kwargs)
        self.makeSubtask("forcedMeasurement",
                         refSchema=afwTable.SourceTable.makeMinimalSchema())

    @pipeBase.timeMethod
    def run(self,
            dia_objects,
            updatedDiaObjectIds,
            expIdBits,
            exposure,
            diffim):
        """Measure forced sources on the direct and difference images.

        Parameters
        ----------
        dia_objects : `pandas.DataFrame`
            Catalog of previously observed and newly created DiaObjects
            contained within the difference and direct images. DiaObjects
            must be indexed on the ``diaObjectId`` column.
        updatedDiaObjectIds : `numpy.ndarray`
            Array of diaObjectIds that were updated during this dia processing.
            Used to assure that the pipeline includes all diaObjects that were
            updated in case one falls on the edge of the CCD.
        expIdBits : `int`
            Bit length of the exposure id.
        exposure : `lsst.afw.image.Exposure`
            Direct image exposure.
        diffim : `lsst.afw.image.Exposure`
            Difference image.

        Returns
        -------
        output_forced_sources : `pandas.DataFrame`
            Catalog of calibrated forced photometered fluxes on both the
            difference and direct images at DiaObject locations.
        """

        afw_dia_objects = self._convert_from_pandas(dia_objects)

        idFactoryDiff = afwTable.IdFactory.makeSource(
            diffim.getInfo().getVisitInfo().getExposureId(),
            afwTable.IdFactory.computeReservedFromMaxBits(int(expIdBits)))

        diffForcedSources = self.forcedMeasurement.generateMeasCat(
            diffim,
            afw_dia_objects,
            diffim.getWcs(),
            idFactory=idFactoryDiff)
        self.forcedMeasurement.run(
            diffForcedSources, diffim, afw_dia_objects, diffim.getWcs())

        directForcedSources = self.forcedMeasurement.generateMeasCat(
            exposure,
            afw_dia_objects,
            exposure.getWcs())
        self.forcedMeasurement.run(
            directForcedSources, exposure, afw_dia_objects, exposure.getWcs())

        output_forced_sources = self._calibrate_and_merge(diffForcedSources,
                                                          directForcedSources,
                                                          diffim,
                                                          exposure)

        output_forced_sources = self._trim_to_exposure(output_forced_sources,
                                                       updatedDiaObjectIds,
                                                       exposure)
        return output_forced_sources.set_index(
            ["diaObjectId", "diaForcedSourceId"],
            drop=False)

    def _convert_from_pandas(self, input_objects):
        """Create minimal schema SourceCatalog from a pandas DataFrame.

        We need a catalog of this type to run within the forced measurement
        subtask.

        Parameters
        ----------
        input_objects : `pandas.DataFrame`
            DiaObjects with locations and ids. ``

        Returns
        -------
        outputCatalog : `lsst.afw.table.SourceTable`
            Output catalog with minimal schema.
        """
        schema = afwTable.SourceTable.makeMinimalSchema()

        outputCatalog = afwTable.SourceCatalog(schema)
        outputCatalog.reserve(len(input_objects))

        for obj_id, df_row in input_objects.iterrows():
            outputRecord = outputCatalog.addNew()
            outputRecord.setId(obj_id)
            outputRecord.setCoord(
                geom.SpherePoint(df_row["ra"],
                                 df_row["decl"],
                                 geom.degrees))
        return outputCatalog

    def _calibrate_and_merge(self,
                             diff_sources,
                             direct_sources,
                             diff_exp,
                             direct_exp):
        """Take the two output catalogs from the ForcedMeasurementTasks and
        calibrate, combine, and convert them to Pandas.

        Parameters
        ----------
        diff_sources : `lsst.afw.table.SourceTable`
            Catalog with PsFluxes measured on the difference image.
        direct_sources : `lsst.afw.table.SourceTable`
            Catalog with PsfFluxes measured on the direct (calexp) image.
        diff_exp : `lsst.afw.image.Exposure`
            Difference exposure ``diff_sources`` were measured on.
        direct_exp : `lsst.afw.image.Exposure`
            Direct (calexp) exposure ``direct_sources`` were measured on.

        Returns
        -------
        output_catalog : `pandas.DataFrame`
            Catalog calibrated diaForcedSources.
        """
        diff_calib = diff_exp.getPhotoCalib()
        direct_calib = direct_exp.getPhotoCalib()

        diff_fluxes = diff_calib.instFluxToNanojansky(diff_sources,
                                                      "slot_PsfFlux")
        direct_fluxes = direct_calib.instFluxToNanojansky(direct_sources,
                                                          "slot_PsfFlux")

        output_catalog = diff_sources.asAstropy().to_pandas()
        output_catalog.rename(columns={"id": "diaForcedSourceId",
                                       "slot_PsfFlux_instFlux": "psFlux",
                                       "slot_PsfFlux_instFluxErr": "psFluxErr",
                                       "slot_Centroid_x": "x",
                                       "slot_Centroid_y": "y"},
                              inplace=True)
        output_catalog.loc[:, "psFlux"] = diff_fluxes[:, 0]
        output_catalog.loc[:, "psFluxErr"] = diff_fluxes[:, 1]

        output_catalog["totFlux"] = direct_fluxes[:, 0]
        output_catalog["totFluxErr"] = direct_fluxes[:, 1]

        visit_info = direct_exp.getInfo().getVisitInfo()
        ccdVisitId = visit_info.getExposureId()
        midPointTaiMJD = visit_info.getDate().get(system=DateTime.MJD)
        output_catalog["ccdVisitId"] = ccdVisitId
        output_catalog["midPointTai"] = midPointTaiMJD
        output_catalog["filterName"] = diff_exp.getFilterLabel().bandLabel

        # Drop superfluous columns from output DataFrame.
        output_catalog.drop(columns=self.config.dropColumns, inplace=True)

        return output_catalog

    def _trim_to_exposure(self, catalog, updatedDiaObjectIds, exposure):
        """Remove DiaForcedSources that are outside of the bounding box region.

        Paramters
        ---------
        catalog : `pandas.DataFrame`
            DiaForcedSources to check against the exposure bounding box.
        updatedDiaObjectIds : `numpy.ndarray`
            Array of diaObjectIds that were updated during this dia processing.
            Used to assure that the pipeline includes all diaObjects that were
            updated in case one falls on the edge of the CCD.
        exposure : `lsst.afw.image.Exposure`
            Exposure to check against.

        Returns
        -------
        output : `pandas.DataFrame`
            DataFrame trimmed to only the objects within the exposure bounding
            box.
        """
        bbox = geom.Box2D(exposure.getBBox())

        xS = catalog.loc[:, "x"]
        yS = catalog.loc[:, "y"]

        return catalog[
            np.logical_or(bbox.contains(xS, yS),
                          np.isin(catalog.loc[:, "diaObjectId"],
                                  updatedDiaObjectIds))]
