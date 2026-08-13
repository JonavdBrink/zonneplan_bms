import os
import yaml
import pytest

def load_fixture_file(filename):
    """Helper to load a YAML/JSON test fixture from tests/fixtures/."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    filepath = os.path.join(current_dir, "fixtures", filename)
    with open(filepath, 'r') as f:
        return yaml.safe_load(f)

def get_forecast_list(data):
    """Helper to cleanly extract a forecast list from loaded fixture dict or list."""
    if isinstance(data, dict):
        if 'attributes' in data and isinstance(data['attributes'], dict):
            attrs = data['attributes']
            if 'schedule' in attrs:
                return attrs['schedule']
            if 'forecast' in attrs:
                return attrs['forecast']
    return data


# Standard HA custom integration enablement
@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in Home Assistant."""
    yield


# Reusable Regression Test Data Fixtures
@pytest.fixture
def july_baseline_forecast():
    """Returns the parsed July 31 baseline forecast list."""
    raw_data = load_fixture_file("july31_data.yaml")
    return get_forecast_list(raw_data)

@pytest.fixture
def august_extremes_forecast():
    """Returns the parsed August 12/13 extreme-price forecast list."""
    raw_data = load_fixture_file("august12_data.yaml")
    return get_forecast_list(raw_data)

@pytest.fixture
def july29_forecast():
    """Returns the parsed July 29 forecast list."""
    raw_data = load_fixture_file("july29_data.yaml")
    return get_forecast_list(raw_data)
