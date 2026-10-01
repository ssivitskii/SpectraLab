import pytest
from spectralab_ml.config import load_project_config
from spectralab_ml.data.reference import load_demo_catalog
from spectralab_ml.datasets import build_demo_dataset
from spectralab_ml.preprocessing import SpectrumPreprocessor


@pytest.fixture(scope="session")
def catalog():
    return load_demo_catalog()


@pytest.fixture(scope="session")
def classes():
    return load_project_config().elements


@pytest.fixture(scope="session")
def processor():
    return SpectrumPreprocessor()


@pytest.fixture(scope="session")
def dataset(catalog, classes, processor):
    return build_demo_dataset(catalog, processor, classes, base_samples=18, variations=2)
