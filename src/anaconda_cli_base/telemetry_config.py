from anaconda_cli_base.config import AnacondaBaseSettings

AUTHENTICATED_ENDPOINT = "https://metrics.aa.anaconda.com"
PUBLIC_ENDPOINT = "https://public.telemetry.anaconda.com"


class TelemetryConfig(AnacondaBaseSettings, table_name="telemetry"):
    """Telemetry configuration.

    Reads from [telemetry] in ~/.anaconda/config.toml.
    Environment variables use ANACONDA_TELEMETRY_ prefix.
    """

    enabled: bool = True
    share_session_identity: bool = True
    skip_internet_check: bool = True
