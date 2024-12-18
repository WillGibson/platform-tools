from unittest.mock import Mock
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from dbt_platform_helper.commands.conduit import conduit
from dbt_platform_helper.providers.secrets import SecretNotFoundException
from tests.platform_helper.conftest import add_addon_config_parameter
from tests.platform_helper.conftest import is_mutmut_test_run


@pytest.mark.parametrize(
    "addon_name",
    [
        "custom-name-postgres",
        "custom-name-rds-postgres",
        "custom-name-redis",
        "custom-name-opensearch",
    ],
)
@patch("dbt_platform_helper.commands.conduit.Conduit")
@patch(
    "dbt_platform_helper.utils.versioning.running_as_installed_package",
    new=Mock(return_value=True),
)
@patch("dbt_platform_helper.commands.conduit.load_application")
def test_start_conduit(mock_application, mock_conduit_object, addon_name, validate_version):
    """Test that given an app, env and addon name strings, the conduit command
    calls start_conduit with app, env, addon type and addon name."""

    mock_conduit_instance = mock_conduit_object.return_value

    result = CliRunner().invoke(
        conduit,
        [
            addon_name,
            "--app",
            "test-application",
            "--env",
            "development",
        ],
    )

    assert result.exit_code == 0

    if_not_mutmut_run_assert_validate_version_called_once(validate_version)
    mock_conduit_instance.start.assert_called_with("development", addon_name, "read")


@patch("dbt_platform_helper.commands.conduit.Conduit")
@patch(
    "dbt_platform_helper.utils.versioning.running_as_installed_package",
    new=Mock(return_value=True),
)
@patch("dbt_platform_helper.commands.conduit.load_application")
@patch("click.secho")
def test_start_conduit_with_exception_raised_exit_1(
    mock_click,
    mock_application,
    mock_conduit_object,
    validate_version,
):

    mock_conduit_instance = mock_conduit_object.return_value
    mock_conduit_instance.start.side_effect = SecretNotFoundException(secret_name="test-secret")
    addon_name = "important-db"
    result = CliRunner().invoke(
        conduit,
        [
            addon_name,
            "--app",
            "test-application",
            "--env",
            "development",
        ],
    )

    mock_click.assert_called_with("""No secret called "test-secret".""", fg="red")

    assert result.exit_code == 1

    if_not_mutmut_run_assert_validate_version_called_once(validate_version)


def if_not_mutmut_run_assert_validate_version_called_once(validate_version):
    # Todo: Get to the bottom of why validate_version_compatibility is not called during a mutmut test run
    if not is_mutmut_test_run():
        if_not_mutmut_run_assert_validate_version_called_once(validate_version)
