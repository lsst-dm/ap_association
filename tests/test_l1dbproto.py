#
# LSST Data Management System
# Copyright 2017 LSST/AURA.
#
# This product includes software developed by the
# LSST Project (http://www.lsst.org/).
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
# You should have received a copy of the LSST License Statement and
# the GNU General Public License along with this program.  If not,
# see <http://www.lsstcorp.org/LegalNotices/>.
#

from __future__ import absolute_import, division, print_function

import os
import numpy as np
import tempfile
import unittest

from lsst.ap.association import \
    AssociationL1DBProtoConfig, \
    AssociationL1DBProtoTask, \
    make_minimal_dia_source_schema
from lsst.afw.cameraGeom.testUtils import DetectorWrapper
import lsst.afw.image as afwImage
import lsst.afw.image.utils as afwImageUtils
import lsst.afw.geom as afwGeom
import lsst.afw.table as afwTable
import lsst.daf.base as dafBase
import lsst.pipe.base as pipeBase
import lsst.utils.tests


def create_test_points(point_locs_deg,
                       start_id=0,
                       schema=None,
                       scatter_arcsec=1.0,
                       indexer_ids=None,
                       associated_ids=None):
    """Create dummy DIASources or DIAObjects for use in our tests.

    Parameters
    ----------
    point_locs_deg : array-like (N, 2) of `float`s
        Positions of the test points to create in RA, DEC.
    start_id : `int`
        Unique id of the first object to create. The remaining sources are
        incremented by one from the first id.
    schema : `lsst.afw.table.Schema`
        Schema of the objects to create. Defaults to the DIASource schema.
    scatter_arcsec : `float`
        Scatter to add to the position of each DIASource.
    indexer_ids : `list` of `ints`s
        Id numbers of pixelization indexer to store. Must be the same length
        as the first dimension of point_locs_deg.
    associated_ids : `list` of `ints`s
        Id numbers of associated DIAObjects to store. Must be the same length
        as the first dimension of point_locs_deg.

    Returns
    -------
    test_points : `lsst.afw.table.SourceCatalog`
        Catalog of points to test.
    """
    if schema is None:
        schema = make_minimal_dia_source_schema()
    sources = afwTable.SourceCatalog(schema)

    for src_idx, (ra, dec,) in enumerate(point_locs_deg):
        src = sources.addNew()
        src['id'] = src_idx + start_id
        coord = afwGeom.SpherePoint(ra, dec, afwGeom.degrees)
        if scatter_arcsec > 0.0:
            coord = coord.offset(
                np.random.rand() * 360 * afwGeom.degrees,
                np.random.rand() * scatter_arcsec * afwGeom.arcseconds)
        if indexer_ids is not None:
            src['pixelId'] = indexer_ids[src_idx]
        if associated_ids is not None:
            src['diaObjectId'] = associated_ids[src_idx]
        src.setCoord(coord)

    return sources


class TestAssociationDBSqlite(unittest.TestCase):

    def setUp(self):
        """Initialize an empty database.
        """

        # CFHT Filters from the camera mapper.
        afwImageUtils.resetFilters()
        afwImageUtils.defineFilter('u', lambdaEff=374, alias="u.MP9301")
        afwImageUtils.defineFilter('g', lambdaEff=487, alias="g.MP9401")
        afwImageUtils.defineFilter('r', lambdaEff=628, alias="r.MP9601")
        afwImageUtils.defineFilter('i', lambdaEff=778, alias="i.MP9701")
        afwImageUtils.defineFilter('z', lambdaEff=1170, alias="z.MP9801")
        afwImageUtils.defineFilter('y', lambdaEff=2000, alias="y.MP9901")

        self.tmp_file, self.db_file = tempfile.mkstemp(dir=os.path.dirname(__file__))
        assoc_db_config = AssociationL1DBProtoConfig()
        assoc_db_config.l1db_config.db_url = 'sqlite:///' + self.db_file
        self.assoc_db = AssociationL1DBProtoTask(config=assoc_db_config)
        self.assoc_db.db.makeSchema(drop=True)

        self.metadata = dafBase.PropertySet()

        self.metadata.set("SIMPLE", "T")
        self.metadata.set("BITPIX", -32)
        self.metadata.set("NAXIS", 2)
        self.metadata.set("NAXIS1", 1024)
        self.metadata.set("NAXIS2", 1153)
        self.metadata.set("RADECSYS", 'FK5')
        self.metadata.set("EQUINOX", 2000.)

        self.metadata.setDouble("CRVAL1", 215.604025685476)
        self.metadata.setDouble("CRVAL2", 53.1595451514076)
        self.metadata.setDouble("CRPIX1", 1109.99981456774)
        self.metadata.setDouble("CRPIX2", 560.018167811613)
        self.metadata.set("CTYPE1", 'RA---SIN')
        self.metadata.set("CTYPE2", 'DEC--SIN')

        self.metadata.setDouble("CD1_1", 5.10808596133527E-05)
        self.metadata.setDouble("CD1_2", 1.85579539217196E-07)
        self.metadata.setDouble("CD2_2", -5.10281493481982E-05)
        self.metadata.setDouble("CD2_1", -8.27440751733828E-07)

        self.wcs = afwGeom.makeSkyWcs(self.metadata)
        self.exposure = afwImage.makeExposure(
            afwImage.makeMaskedImageFromArrays(np.ones((1024, 1153))),
            self.wcs)
        detector = DetectorWrapper(id=23, bbox=self.exposure.getBBox()).detector
        visit = afwImage.VisitInfo(
            exposureId=4321,
            exposureTime=200.,
            date=dafBase.DateTime(nsecs=1400000000 * 10**9))
        self.exposure.setDetector(detector)
        self.exposure.getInfo().setVisitInfo(visit)
        self.exposure.setFilter(afwImage.Filter('g'))
        self.flux0 = 10000
        self.flux0_err = 100
        self.exposure.getCalib().setFluxMag0((self.flux0, self.flux0_err))

        bbox = afwGeom.Box2D(self.exposure.getBBox())
        wcs = self.exposure.getWcs()
        self.expMd = pipeBase.Struct(
            bbox=bbox,
            wcs=wcs,)

    def tearDown(self):
        """Close the database connection and delete the object.
        """
        del self.tmp_file
        os.remove(self.db_file)
        del self.assoc_db

    def _compare_source_records(self, record_a, record_b):
        """Compare the values stored in two source records.

        This comparison assumes that the schema for record_a is a
        subset of or equal to the schema of record_b.

        Parameters
        ----------
        record_a : `lsst.afw.table.SourceRecord`
        record_b : `lsst.afw.table.SourceRecord`
        """
        for sub_schema in record_a.schema:
            value_a = record_a[sub_schema.getKey()]
            value_b = record_a[sub_schema.getKey()]
            if sub_schema.getField().getTypeString() == 'Angle':
                value_a = value_a.asDegrees()
                value_b = value_b.asDegrees()

            if sub_schema.getField().getTypeString()[0] == 'S':
                self.assertEqual(value_a, value_b)
            elif np.isfinite(value_a) and np.isfinite(value_b):
                if sub_schema.getField().getTypeString() == 'L':
                    self.assertEqual(value_a, value_b)
                else:
                    self.assertAlmostEqual(value_a, value_b)
            else:
                self.assertFalse(np.isfinite(value_a))
                self.assertFalse(np.isfinite(value_b))

    def test_load_dia_objects(self):
        """Test the retrieval of DIAObjects from the database.
        """
        # Create DIAObjects with real positions on the sky with the first
        # point out of the CCD bounding box.
        n_objects = 10
        n_missing_objects = 1
        # Loop backward so the missing point is last.
        object_centers = [
            [self.wcs.pixelToSky(idx, idx).getRa().asDegrees(),
             self.wcs.pixelToSky(idx, idx).getDec().asDegrees()]
            for idx in reversed(np.linspace(-10, 1000, n_objects))]
        dia_objects = create_test_points(
            point_locs_deg=object_centers,
            start_id=0,
            schema=self.assoc_db.dia_object_afw_schema,
            scatter_arcsec=-1)
        for src_idx, dia_object in enumerate(dia_objects):
            dia_object['psFluxMean_g'] = 10000. + np.random.randn() * 100.
            dia_object['psFluxMeanErr_g'] = 100. + np.random.randn() * 10.
            dia_object['psFluxSigma_g'] = 100. + np.random.randn() * 10.

        # Store the DIAObjects.
        self.assoc_db.store_dia_objects(dia_objects, True, self.exposure)

        # Load the DIAObjects using the bounding box and WCS associated with
        # them.
        output_dia_objects = self.assoc_db.load_dia_objects(self.exposure)
        # One of the objects should be outside of the bounding box and will
        # therefore not be loaded.
        self.assertEqual(len(output_dia_objects),
                         n_objects - n_missing_objects)

        # Loop over the 9 output_dia_objects
        for dia_object, created_object in zip(output_dia_objects, dia_objects):
            # HTM trixel for this CCD at level 7.
            created_object["pixelId"] = 225823
            self._compare_source_records(dia_object, created_object)

    def test_store_dia_objects_no_indexer_id_update(self):
        """Test the storage and retrieval of DIAObjects from the database
        without updating their HTM index.
        """
        # Create DIAObjects with real positions on the sky.
        n_objects = 5
        object_centers = [
            [self.wcs.pixelToSky(idx, idx).getRa().asDegrees(),
             self.wcs.pixelToSky(idx, idx).getDec().asDegrees()]
            for idx in np.linspace(1, 1000, 10)[:n_objects]]
        dia_objects = create_test_points(
            point_locs_deg=object_centers,
            start_id=0,
            schema=self.assoc_db.dia_object_afw_schema,
            scatter_arcsec=1.0)
        for src_idx, dia_object in enumerate(dia_objects):
            dia_object['psFluxMean_g'] = 10000. + np.random.randn() * 100.
            dia_object['psFluxMeanErr_g'] = 100. + np.random.randn() * 10.
            dia_object['psFluxSigma_g'] = 100. + np.random.randn() * 10.
            dia_object['pixelId'] = 999999

        # Store their values and test if they are preserved after round tripping
        # to the DB.
        self.assoc_db.store_dia_objects(dia_objects, False, self.exposure)
        output_dia_objects = output_dia_objects = self.assoc_db.db.getDiaObjects(
            [[999999, 999999 + 1]])
        self.assertEqual(len(output_dia_objects), len(dia_objects))
        for dia_object, created_object in zip(output_dia_objects, dia_objects):
            self._compare_source_records(dia_object, created_object)

    def test_store_dia_objects_indexer_id_update(self):
        """Test the storage and retrieval of DIAObjects from the database
        while updating their HTM index.
        """

        # Create DIAObjects with real positions on the sky.
        n_objects = 5
        object_centers = [
            [self.wcs.pixelToSky(idx, idx).getRa().asDegrees(),
             self.wcs.pixelToSky(idx, idx).getDec().asDegrees()]
            for idx in np.linspace(1, 1000, 10)[:n_objects]]
        dia_objects = create_test_points(
            point_locs_deg=object_centers,
            start_id=0,
            schema=self.assoc_db.dia_object_afw_schema,
            scatter_arcsec=1.0)
        # Store and overwrite the same sources this time updating their HTM
        # index.
        for src_idx, dia_object in enumerate(dia_objects):
            dia_object['psFluxMean_g'] = 10000. + np.random.randn() * 100.
            dia_object['psFluxMeanErr_g'] = 100. + np.random.randn() * 10.
            dia_object['psFluxSigma_g'] = 100. + np.random.randn() * 10.
        self.assoc_db.store_dia_objects(dia_objects, True, self.exposure)

        # Retrieve the DIAObjects again and test that their HTM index has
        # been updated properly.
        output_dia_objects = self.assoc_db.db.getDiaObjects(
            [[225823, 225823 + 1]])
        self.assertEqual(len(output_dia_objects), len(dia_objects))
        for dia_object, created_object in zip(output_dia_objects, dia_objects):
            # HTM trixel for this CCD at level 7.
            created_object["pixelId"] = 225823
            self._compare_source_records(dia_object, created_object)

    def test_indexer_ids(self):
        """Test that the returned HTM pixel indices are returned as expected.
        """
        n_objects = 5
        object_centers = [[0.1 * idx, 0.1 * idx] for idx in range(n_objects)]
        dia_objects = create_test_points(
            point_locs_deg=object_centers,
            start_id=0,
            schema=self.assoc_db.dia_object_afw_schema,
            scatter_arcsec=-1)
        expected_ids = [131072, 253952, 253952, 253952, 253955]
        for obj, indexer_id in zip(dia_objects, expected_ids):
            self.assertEqual(self.assoc_db.compute_indexer_id(obj.getCoord()),
                             indexer_id)

    def test_load_dia_sources(self):
        """Test the retrieval of DIASources from the database.
        """
        n_sources = 5
        dia_sources = create_test_points(
            point_locs_deg=[[0.1, 0.1] for idx in range(n_sources)],
            start_id=0,
            schema=self.assoc_db.dia_source_afw_schema,
            scatter_arcsec=1.0,
            associated_ids=range(n_sources))

        for dia_source in dia_sources:
            dia_source['psFlux'] = 10000. + np.random.randn() * 100.
            dia_source['psFluxErr'] = 100. + np.random.randn() * 10.
            dia_source['filterName'] = self.exposure.getFilter().getName()

        # Store the first set of DIASources and retrieve them using their
        # associated DIAObject id.
        self.assoc_db.store_dia_sources(dia_sources,
                                        range(n_sources),
                                        self.exposure)

        for dia_source in dia_sources:
            tmp_flux = dia_source['psFlux']
            tmp_flux_err = dia_source['psFluxErr']
            dia_source['psFlux'] = tmp_flux / self.flux0
            dia_source['psFluxErr'] = np.sqrt(
                (tmp_flux_err / self.flux0) ** 2 +
                (tmp_flux * self.flux0_err / self.flux0 ** 2) ** 2)
            dia_source['ccdVisitId'] = \
                self.exposure.getInfo().getVisitInfo().getExposureId()

        for dia_object_id, dia_source in zip(range(n_sources), dia_sources):
            stored_dia_sources = self.assoc_db.load_dia_sources([dia_object_id])
            # Should load only one object.
            self.assertEqual(len(stored_dia_sources), 1)
            self._compare_source_records(stored_dia_sources[0], dia_source)

        # Load all stored DIASources at once.
        stored_dia_sources = self.assoc_db.load_dia_sources(range(n_sources))
        self.assertEqual(len(stored_dia_sources), n_sources)
        for dia_source, created_source in zip(stored_dia_sources, dia_sources):
            self._compare_source_records(dia_source, created_source)

        # Test that asking for an id that has no associated sources returns
        # and empty catalog.
        empty_dia_sources = self.assoc_db.load_dia_sources([6])
        self.assertEqual(len(empty_dia_sources), 0)

    def test_store_dia_sources_different_schema(self):
        """Test the storage of DIASources in the database.
        """
        # Create a schema that is miss-matched to the expected DIASource
        # schema but with expected ipdiffim like flux columns. Also add
        # unused columns that are ignored within the code.
        schema = afwTable.SourceTable.makeMinimalSchema()
        schema.addField('base_PsfFlux_instFlux', type='D')
        schema.addField('base_PsfFlux_instFluxErr', type='D')
        schema.addField('junk1', type='L')
        schema.addField('junk2', type='D')
        schema.addField('junk3', type='L')
        schema.addField('junk4', type='D')

        # Create test associated DIASources.
        n_sources = 5
        source_centers = [[1. * idx, 1. * idx] for idx in range(n_sources)]
        dia_sources = create_test_points(
            point_locs_deg=source_centers,
            start_id=0,
            schema=schema,
            scatter_arcsec=-1)
        for dia_source in dia_sources:
            dia_source['base_PsfFlux_instFlux'] = 10000.
            dia_source['base_PsfFlux_instFluxErr'] = 100.

        # Check the DIASources round trip properly. We don't need to be
        # as complex here as the call signature has been almost fully tested
        # here by the ``test_store_catalog_dia_sources`` tests.
        self.assoc_db.store_dia_sources(dia_sources,
                                        range(5),
                                        self.exposure)
        round_trip_dia_source_catalog = self.assoc_db.db.getDiaSources(
            range(5), dt=None)

        # Remake the DIASources with the correct values and columns for
        # comparison.
        dia_sources = create_test_points(
            point_locs_deg=source_centers,
            start_id=0,
            schema=make_minimal_dia_source_schema(),
            scatter_arcsec=-1,
            associated_ids=range(5))
        for dia_source in dia_sources:
            dia_source['filterName'] = self.exposure.getFilter().getName()
            dia_source['ccdVisitId'] = \
                self.exposure.getInfo().getVisitInfo().getExposureId()
            dia_source['psFlux'] = 10000. / self.flux0
            dia_source['psFluxErr'] = np.sqrt(
                (100. / self.flux0) ** 2 +
                (10000. * self.flux0_err / self.flux0 ** 2) ** 2)

        for stored_dia_source, dia_source in zip(round_trip_dia_source_catalog,
                                                 dia_sources):

            self._compare_source_records(stored_dia_source, dia_source)

    def test_store_dia_sources(self):
        """Test the storage of DIASources in the database.
        """
        # Create test associated DIAObjects and DIASources.
        n_sources = 5
        source_centers = [[1. * idx, 1. * idx] for idx in range(n_sources)]
        obj_ids = [idx for idx in range(n_sources)]
        dia_sources = create_test_points(
            point_locs_deg=source_centers,
            start_id=0,
            schema=self.assoc_db.dia_source_afw_schema,
            scatter_arcsec=1.0,
            associated_ids=range(5))
        for dia_source in dia_sources:
            dia_source['psFlux'] = 10000. + np.random.randn() * 100.
            dia_source['psFluxErr'] = 100. + np.random.randn() * 10.

        # Check the DIASources round trip properly. We don't need to be
        # as complex here as the call signature has been almost fully tested
        # here by the ``test_store_catalog_dia_sources`` tests.
        self.assoc_db.store_dia_sources(dia_sources,
                                        range(5),
                                        self.exposure)
        round_trip_dia_source_catalog = self.assoc_db.db.getDiaSources(
            range(5), dt=None)

        for stored_dia_source, dia_source, obj_id in zip(
                round_trip_dia_source_catalog,
                dia_sources,
                obj_ids):
            dia_source['diaObjectId'] = obj_id
            tmp_flux = dia_source['psFlux']
            tmp_flux_err = dia_source['psFluxErr']
            dia_source['psFlux'] = tmp_flux / self.flux0
            dia_source['psFluxErr'] = np.sqrt(
                (tmp_flux_err / self.flux0) ** 2 +
                (tmp_flux * self.flux0_err / self.flux0 ** 2) ** 2)
            dia_source['filterName'] = self.exposure.getFilter().getName()
            dia_source['ccdVisitId'] = \
                self.exposure.getInfo().getVisitInfo().getExposureId()

            self._compare_source_records(dia_source, stored_dia_source)


class MemoryTester(lsst.utils.tests.MemoryTestCase):
    pass


def setup_module(module):
    lsst.utils.tests.init()


if __name__ == "__main__":
    lsst.utils.tests.init()
    unittest.main()