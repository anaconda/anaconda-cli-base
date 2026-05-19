from anaconda_cli_base.config import AnacondaBaseSettings

DEFAULT_TELEMETRY_ENDPOINT = "https://metrics.aa.anaconda.com"
PUBLIC_TELEMETRY_ENDPOINT = "https://public.telemetry.anaconda.com"


class TelemetryConfig(AnacondaBaseSettings, table_name="telemetry"):
    """Telemetry configuration.

    Reads from [telemetry] in ~/.anaconda/config.toml.
    Environment variables use ANACONDA_TELEMETRY_ prefix.
    """

    endpoint: str = DEFAULT_TELEMETRY_ENDPOINT
    public_endpoint: str = PUBLIC_TELEMETRY_ENDPOINT
    anon_usage: bool = True
    skip_internet_check: bool = True
