import pytest
import time
from fault_control.toxiproxy import ToxiproxyController

TOXI = ToxiproxyController()

@pytest.fixture(autouse=True)
def clean_toxics():
    TOXI.reset_all()
    yield
    TOXI.reset_all()

@pytest.fixture
def toxi():
    return TOXI
