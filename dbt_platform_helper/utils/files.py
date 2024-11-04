import os
from copy import deepcopy
from datetime import datetime
from os import makedirs
from pathlib import Path

import click
import yaml
from jinja2 import Environment
from jinja2 import FileSystemLoader

platform_helper_cache_file = ".platform-helper-config.yml"


def to_yaml(value):
    return yaml.dump(value, sort_keys=False)


def mkfile(base_path, file_path, contents, overwrite=False):
    file_path = Path(file_path)
    file = Path(base_path).joinpath(file_path)
    file_exists = file.exists()

    if not file_path.parent.exists():
        makedirs(file_path.parent)

    if file_exists and not overwrite:
        return f"File {file_path} exists; doing nothing"

    action = "overwritten" if file_exists and overwrite else "created"

    file.write_text(contents)

    return f"File {file_path} {action}"


def generate_override_files(base_path, file_path, output_dir):
    def generate_files_for_dir(pattern):
        for file in file_path.glob(pattern):
            if file.is_file():
                contents = file.read_text()
                file_name = str(file).removeprefix(f"{file_path}/")
                click.echo(
                    mkfile(
                        base_path,
                        output_dir / file_name,
                        contents,
                        overwrite=True,
                    )
                )

    generate_files_for_dir("*")
    generate_files_for_dir("bin/*")


def generate_override_files_from_template(base_path, overrides_path, output_dir, template_data={}):
    templates = Environment(
        loader=FileSystemLoader(f"{overrides_path}"), keep_trailing_newline=True
    )
    environments = ",".join([env["name"] for env in template_data["environments"]])
    data = {"environments": environments}

    def generate_files_for_dir(pattern):

        for file in overrides_path.glob(pattern):
            if file.is_file():
                file_name = str(file).removeprefix(f"{overrides_path}/")
                contents = templates.get_template(str(file_name)).render(data)
                message = mkfile(base_path, output_dir / file_name, contents, overwrite=True)
                click.echo(message)

    generate_files_for_dir("*")
    generate_files_for_dir("bin/*")


def apply_environment_defaults(config):
    if "environments" not in config:
        return config

    enriched_config = deepcopy(config)

    environments = enriched_config["environments"]
    env_defaults = environments.get("*", {})
    without_defaults_entry = {
        name: data if data else {} for name, data in environments.items() if name != "*"
    }

    default_versions = config.get("default_versions", {})

    def combine_env_data(data):
        return {
            **env_defaults,
            **data,
            "versions": {
                **default_versions,
                **env_defaults.get("versions", {}),
                **data.get("versions", {}),
            },
        }

    defaulted_envs = {
        env_name: combine_env_data(env_data)
        for env_name, env_data in without_defaults_entry.items()
    }

    enriched_config["environments"] = defaulted_envs

    return enriched_config


def read_supported_versions_from_cache(resource_name):

    platform_helper_config = read_file_as_yaml(platform_helper_cache_file)

    return platform_helper_config.get(resource_name).get("versions")


def write_to_cache(resource_name, supported_versions):

    platform_helper_config = {}

    if os.path.exists(platform_helper_cache_file):
        platform_helper_config = read_file_as_yaml(platform_helper_cache_file)

    cache_dict = {
        resource_name: {
            "versions": supported_versions,
            "date-retrieved": datetime.now().strftime("%d-%m-%y %H:%M:%S"),
        }
    }

    platform_helper_config.update(cache_dict)

    with open(platform_helper_cache_file, "w") as file:
        file.write("# [!] This file is autogenerated via the platform-helper. Do not edit.\n")
        yaml.dump(platform_helper_config, file)


def cache_refresh_required(resource_name) -> bool:
    """
    Checks if the platform-helper should reach out to AWS to 'refresh' its
    cached values.

    An API call is needed if any of the following conditions are met:
        1. No cache file (.platform-helper-config.yml) exists.
        2. The resource name (e.g. redis, opensearch) does not exist within the cache file.
        3. The date-retrieved value of the cached data is > than a time interval. In this case 1 day.
    """

    if not os.path.exists(platform_helper_cache_file):
        return True

    platform_helper_config = read_file_as_yaml(platform_helper_cache_file)

    if platform_helper_config.get(resource_name):
        return check_if_cached_datetime_is_greater_than_interval(
            platform_helper_config[resource_name].get("date-retrieved"), 1
        )


def check_if_cached_datetime_is_greater_than_interval(date_retrieved, interval_in_days):

    current_datetime = datetime.now()
    cached_datetime = datetime.strptime(date_retrieved, "%d-%m-%y %H:%M:%S")
    delta = current_datetime - cached_datetime

    return False if delta.days < interval_in_days else True


def read_file_as_yaml(file_name):

    data = {}

    with open(file_name, "r") as file:
        data = yaml.safe_load(file)

    return data
