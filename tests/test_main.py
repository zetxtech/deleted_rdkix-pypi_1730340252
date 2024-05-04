def test_descriptor():
    from rdkix.Chem import Descriptors

    assert len(Descriptors._descList) == 209


def test_3d_descriptors():
    from rdkix import Chem
    from rdkix.Chem import AllChem, Descriptors3D

    m2 = Chem.AddHs(Chem.MolFromSmiles("CC"))
    AllChem.EmbedMolecule(m2, randomSeed=1)
    assert round(Descriptors3D.NPR1(m2), 10) == 0.2553516286


def test_data_dir_and_chemical_features():
    """Checks if data directory is correctly set
    and if ChemicalFeatures work
    """
    import os

    from rdkix import Chem, RDConfig
    from rdkix.Chem import ChemicalFeatures

    fdefName = os.path.join(RDConfig.RDDataDir, "BaseFeatures.fdef")
    factory = ChemicalFeatures.BuildFeatureFactory(fdefName)
    m = Chem.MolFromSmiles("OCc1ccccc1CN")
    feats = factory.GetFeaturesForMol(m)
    assert len(feats) == 8


def test_rdkix_chem_draw_import():
    # This segfaults if the compiled cairo version from centos is used
    from rdkix.Chem.Draw import ReactionToImage  # noqa: F401
