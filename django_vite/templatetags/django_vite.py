import json
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Type, Union
from urllib.parse import urljoin

from django import template
from django.conf import settings
from django.utils.safestring import mark_safe

register = template.Library()


class DjangoViteManifest(NamedTuple):
    """
    Represent an entry for a file inside the "manifest.json".
    """

    file: str
    src: str
    isEntry: Optional[bool] = false
    css: Optional[List[str]] = []
    imports: Optional[List[str]] = []


class DjangoViteConfig(NamedTuple):
    """
    Represent the Django Vite configuration structure.
    """

    # Location of Vite compiled assets (only used in Vite production mode).
    assets_path: Union[Path, str]

    # If using in development or production mode.
    dev_mode: bool = False

    # Default Vite server protocol (http or https)
    dev_server_protocol: str = "http"

    # Default vite server hostname.
    dev_server_host: str = "localhost"

    # Default Vite server port.
    dev_server_port: int = 3000

    # Default Vite server path to HMR script.
    ws_client_url: str = "@vite/client"

    # Prefix for STATIC_URL.
    static_url_prefix: str = ""

    # Motif in the "manifest.json" to find the polyfills generated by Vite.
    legacy_polyfills_motif: str = "legacy-polyfills"

    # Path to your manifest file generated by Vite.
    manifest_path: Union[Path, str] = ""

    @property
    def static_root(self) -> Union[Path, str]:
        """
        Compute the static root URL of assets.

        Returns:
            Union[Path, str] -- Static root URL.
        """

        return (
            self.assets_path
            if self.dev_mode
            else Path(settings.STATIC_ROOT) / self.static_url_prefix
        )

    def get_computed_manifest_path(self) -> Union[Path, str]:
        """
        Compute the path to the "manifest.json".

        Returns:
            Union[Path, str] -- Path to the "manifest.json".
        """

        return (
            self.manifest_path
            if self.manifest_path
            else self.static_root / "manifest.json"
        )


class DjangoViteAssetLoader:
    """
    Class handling Vite asset loading.
    """

    _instance = None

    _configs = Dict[str, Type[DjangoViteConfig]]
    _manifests: Dict[str, Type[DjangoViteManifest]]
    _static_urls: Dict[str, str]

    def __init__(self) -> None:
        raise RuntimeError("Use the instance() method instead.")

    def generate_vite_asset(
        self,
        path: str,
        config_key: str,
        **kwargs: Dict[str, str],
    ) -> str:
        """
        Generates a <script> tag for this JS/TS asset and a <link> tag for
        all of its CSS dependencies by reading the manifest
        file (for production only).
        In development Vite loads all by itself.

        Arguments:
            path {str} -- Path to a Vite JS/TS asset to include.
            config_key {str} -- Key of the configuration to use.

        Returns:
            str -- All tags to import this file in your HTML page.

        Keyword Arguments:
            **kwargs {Dict[str, str]} -- Adds new attributes to generated
                script tags.

        Raises:
            RuntimeError: If cannot find the file path in the
                manifest (only in production).

        Returns:
            str -- The <script> tag and all <link> tags to import
                this asset in your page.
        """

        config = self._get_config(config_key)
        static_url = self._get_static_url(config_key)

        if config.dev_mode:
            return DjangoViteAssetLoader._generate_script_tag(
                DjangoViteAssetLoader._generate_vite_server_url(
                    path, static_url, config
                ),
                {"type": "module"},
            )

        manifest = self._get_manifest(config_key)

        if path not in manifest:
            raise RuntimeError(
                f"Cannot find {path} in Vite manifest "
                f"at {config.get_computed_manifest_path()}"
            )

        tags = []
        scripts_attrs = {"type": "module", "crossorigin": "", **kwargs}

        # Add dependent CSS
        tags.extend(self._generate_css_files_of_asset(path, config_key, []))

        # Add the script by itself
        tags.append(
            DjangoViteAssetLoader._generate_script_tag(
                urljoin(static_url, manifest[path].file),
                attrs=scripts_attrs,
            )
        )

        return "\n".join(tags)

    def _generate_css_files_of_asset(
        self,
        path: str,
        config_key: str,
        already_processed: List[str],
    ) -> List[str]:
        """
        Generates all CSS tags for dependencies of an asset.

        Arguments:
            path {str} -- Path to an asset in the 'manifest.json'.
            config_key {str} -- Key of the configuration to use.
            already_processed {list} -- List of already processed CSS file.

        Returns:
            list -- List of CSS tags.
        """

        tags = []
        static_url = self._get_static_url(config_key)
        manifest = self._get_manifest(config_key)
        manifest_entry = manifest[path]

        for import_path in manifest_entry.imports:
            tags.extend(
                self._generate_css_files_of_asset(
                    import_path, already_processed
                )
            )

        for css_path in manifest_entry.css:
            if css_path not in already_processed:
                tags.append(
                    DjangoViteAssetLoader._generate_stylesheet_tag(
                        urljoin(static_url, css_path)
                    )
                )

            already_processed.append(css_path)

        return tags

    def generate_vite_asset_url(self, path: str, config_key: str) -> str:
        """
        Generates only the URL of an asset managed by ViteJS.
        Warning, this function does not generate URLs for dependant assets.

        Arguments:
            path {str} -- Path to a Vite asset.
            config_key {str} -- Key of the configuration to use.

        Raises:
            RuntimeError: If cannot find the asset path in the
                manifest (only in production).

        Returns:
            str -- The URL of this asset.
        """

        config = self._get_config(config_key)
        static_url = self._get_static_url(config_key)

        if config.dev_mode:
            return DjangoViteAssetLoader._generate_vite_server_url(
                path, config
            )

        manifest = self._get_manifest(config_key)

        if path not in manifest:
            raise RuntimeError(
                f"Cannot find {path} in Vite manifest "
                f"at {config.get_computed_manifest_path()}"
            )

        return urljoin(static_url, manifest[path].file)

    def generate_vite_legacy_polyfills(
        self,
        config_key: str,
        **kwargs: Dict[str, str],
    ) -> str:
        """
        Generates a <script> tag to the polyfills
        generated by '@vitejs/plugin-legacy' if used.
        This tag must be included at end of the <body> before
        including other legacy scripts.

        Arguments:
            config_key {str} -- Key of the configuration to use.

        Keyword Arguments:
            **kwargs {Dict[str, str]} -- Adds new attributes to generated
                script tags.

        Raises:
            RuntimeError: If polyfills path not found inside
                the 'manifest.json' (only in production).

        Returns:
            str -- The script tag to the polyfills.
        """

        config = self._get_config(config_key)
        manifest = self._get_manifest(config_key)
        static_url = self._get_static_url(config_key)

        if config.dev_mode:
            return ""

        scripts_attrs = {"nomodule": "", "crossorigin": "", **kwargs}

        for path, content in manifest.items():
            if config.legacy_polyfills_motif in path:
                return DjangoViteAssetLoader._generate_script_tag(
                    urljoin(static_url, content.file),
                    attrs=scripts_attrs,
                )

        raise RuntimeError(
            f"Vite legacy polyfills not found in manifest "
            f"at {config.get_computed_manifest_path()}"
        )

    def generate_vite_legacy_asset(
        self,
        path: str,
        config_key: str,
        **kwargs: Dict[str, str],
    ) -> str:
        """
        Generates a <script> tag for legacy assets JS/TS
        generated by '@vitejs/plugin-legacy'
        (in production only, in development do nothing).

        Arguments:
            path {str} -- Path to a Vite asset to include
                (must contains '-legacy' in its name).
            config_key {str} -- Key of the configuration to use.

        Keyword Arguments:
            **kwargs {Dict[str, str]} -- Adds new attributes to generated
                script tags.

        Raises:
            RuntimeError: If cannot find the asset path in the
                manifest (only in production).

        Returns:
            str -- The script tag of this legacy asset .
        """

        config = self._get_config(config_key)
        static_url = self._get_static_url(config_key)

        if config.dev_mode:
            return ""

        manifest = self._get_manifest(config_key)

        if path not in manifest:
            raise RuntimeError(
                f"Cannot find {path} in Vite manifest "
                f"at {config.get_computed_manifest_path()}"
            )

        scripts_attrs = {"nomodule": "", "crossorigin": "", **kwargs}

        return DjangoViteAssetLoader._generate_script_tag(
            urljoin(static_url, manifest[path].file),
            attrs=scripts_attrs,
        )

    def _get_config(self, config_key: str) -> Type[DjangoViteConfig]:
        """
        Get configuration object registered with the key passed in
        parameter.

        Arguments:
            config_key {str} -- Key of the configuration to retrieve.

        Raises:
            RuntimeError: If no configuration exists for this key.

        Returns:
            Type[DjangoViteConfig] -- The configuration.
        """

        if config_key not in self._configs:
            raise RuntimeError(f'Cannot find "{config_key}" configuration')

        return self._configs[config_key]

    def _parse_manifest(
        self, config_key: str
    ) -> Dict[str, Type[DjangoViteManifest]]:
        """
        Read and parse the Vite manifest file.

        Arguments:
            config_key {str} -- Key of the configuration to use.

        Raises:
            RuntimeError: if cannot load the file or JSON in file is malformed.
        """

        config = self._get_config(config_key)

        try:
            with open(
                config.get_computed_manifest_path(), "r"
            ) as manifest_file:
                manifest_content = manifest_file.read()
                manifest_json = json.loads(manifest_content)

                return {
                    k: DjangoViteManifest(**v)
                    for k, v in manifest_json.items()
                }

        except Exception as error:
            raise RuntimeError(
                f"Cannot read Vite manifest file at "
                f"{config.get_computed_manifest_path()} : {str(error)}"
            )

    def _get_manifest(
        self, config_key: str
    ) -> Dict[str, Type[DjangoViteManifest]]:
        """
        Load if needed and parse the "manifest.json" of the specified
        configuration.

        Arguments:
            config_key {str} -- Key of the configuration to use.

        Returns:
            Dict[str, Type[DjangoViteManifest]] -- Parsed content of
                the "manifest.json"
        """

        if config_key not in self._manifests:
            self._manifests[config_key] = self._parse_manifest(config_key)

        return self._manifests[config_key]

    def _get_static_url(self, config_key: str) -> str:
        """
        Build the static URL of a specified configuration.

        Arguments:
            config_key {str} -- Key of the configuration to use.

        Returns:
            str -- The static URL.
        """

        if config_key not in self._static_urls:
            config = self._get_config(config_key)
            static_url = urljoin(settings.STATIC_URL, config.static_url_prefix)

            self._static_urls[config_key] = (
                static_url if static_url[-1] == "/" else static_url + "/"
            )

        return self._static_urls[config_key]

    @classmethod
    def instance(cls):
        """
        Singleton.
        Uses singleton to keep parsed manifests in memory after
        the first time they are loaded.

        Returns:
            DjangoViteAssetLoader -- only instance of the class.
        """

        if cls._instance is None:
            cls._instance = cls.__new__(cls)
            cls._instance._configs = {}
            cls._instance._manifests = {}
            cls._instance._static_urls = {}

            if hasattr(settings, "DJANGO_VITE"):
                config = getattr(settings, "DJANGO_VITE")

                for config_key, config_values in config.items():
                    if isinstance(config_values, DjangoViteConfig):
                        cls._instance._configs[config_key] = config_values
                    elif isinstance(config_values, dict):
                        cls._instance._configs[config_key] = DjangoViteConfig(
                            **config_values
                        )
                    else:
                        raise RuntimeError(
                            f"Cannot read configuration for key '{config_key}'"
                        )
            else:
                # Warning : This branch will be remove in further
                # releases. Please use new way of handling configuration.

                _config_keys = {
                    "DJANGO_VITE_DEV_MODE": "dev_mode",
                    "DJANGO_VITE_DEV_SERVER_PROTOCOL": "dev_server_protocol",
                    "DJANGO_VITE_DEV_SERVER_HOST": "dev_server_host",
                    "DJANGO_VITE_DEV_SERVER_PORT": "dev_server_port",
                    "DJANGO_VITE_WS_CLIENT_URL": "ws_client_url",
                    "DJANGO_VITE_ASSETS_PATH": "assets_path",
                    "DJANGO_VITE_STATIC_URL_PREFIX": "static_url_prefix",
                    "DJANGO_VITE_MANIFEST_PATH": "manifest_path",
                    "DJANGO_VITE_LEGACY_POLYFILLS_MOTIF": "legacy_polyfills_motif",
                }

                config = {
                    _config_keys[setting_key]: getattr(settings, setting_key)
                    for setting_key in dir(settings)
                    if setting_key in _config_keys.keys()
                }

                cls._instance._configs["default"] = DjangoViteConfig(**config)

        return cls._instance

    @classmethod
    def generate_vite_ws_client(cls, config_key: str) -> str:
        """
        Generates the script tag for the Vite WS client for HMR.
        Only used in development, in production this method returns
        an empty string.

        Arguments:
            config_key {str} -- Key of the configuration to use.

        Returns:
            str -- The script tag or an empty string.
        """

        config = cls.instance()._get_config(config_key)
        static_url = cls.instance()._get_static_url(config_key)

        if not config.dev_mode:
            return ""

        return cls._generate_script_tag(
            cls._generate_vite_server_url(
                config.ws_client_url, static_url, config
            ),
            {"type": "module"},
        )

    @staticmethod
    def _generate_script_tag(src: str, attrs: Dict[str, str]) -> str:
        """
        Generates an HTML script tag.

        Arguments:
            src {str} -- Source of the script.

        Keyword Arguments:
            attrs {Dict[str, str]} -- List of custom attributes
                for the tag.

        Returns:
            str -- The script tag.
        """

        attrs_str = " ".join(
            [f'{key}="{value}"' for key, value in attrs.items()]
        )

        return f'<script {attrs_str} src="{src}"></script>'

    @staticmethod
    def _generate_stylesheet_tag(href: str) -> str:
        """
        Generates and HTML <link> stylesheet tag for CSS.

        Arguments:
            href {str} -- CSS file URL.

        Returns:
            str -- CSS link tag.
        """

        return f'<link rel="stylesheet" href="{href}" />'

    @staticmethod
    def _generate_vite_server_url(
        path: str,
        static_url: str,
        config: Type[DjangoViteConfig],
    ) -> str:
        """
        Generates an URL to and asset served by the Vite development server.

        Keyword Arguments:
            path {str} -- Path to the asset.
            config {Type[DjangoViteConfig]} -- Config object to use.

        Returns:
            str -- Full URL to the asset.
        """

        return urljoin(
            f"{config.dev_server_protocol}://"
            f"{config.dev_server_host}:{config.dev_server_port}",
            urljoin(static_url, path),
        )


# Make Loader instance at startup to prevent threading problems
DjangoViteAssetLoader.instance()


@register.simple_tag
@mark_safe
def vite_hmr_client(config: str = "default") -> str:
    """
    Generates the script tag for the Vite WS client for HMR.
    Only used in development, in production this method returns
    an empty string.

    Arguments:
        config {str} -- Configuration to use.

    Returns:
        str -- The script tag or an empty string.
    """

    return DjangoViteAssetLoader.generate_vite_ws_client(config)


@register.simple_tag
@mark_safe
def vite_asset(
    path: str,
    config: str = "default",
    **kwargs: Dict[str, str],
) -> str:
    """
    Generates a <script> tag for this JS/TS asset and a <link> tag for
    all of its CSS dependencies by reading the manifest
    file (for production only).
    In development Vite loads all by itself.

    Arguments:
        path {str} -- Path to a Vite JS/TS asset to include.
        config {str} -- Configuration to use.

    Returns:
        str -- All tags to import this file in your HTML page.

    Keyword Arguments:
        **kwargs {Dict[str, str]} -- Adds new attributes to generated
            script tags.

    Raises:
        RuntimeError: If cannot find the file path in the
            manifest (only in production).

    Returns:
        str -- The <script> tag and all <link> tags to import this
            asset in your page.
    """

    assert path is not None
    assert config is not None

    return DjangoViteAssetLoader.instance().generate_vite_asset(
        path, config, **kwargs
    )


@register.simple_tag
def vite_asset_url(path: str, config: str = "default") -> str:
    """
    Generates only the URL of an asset managed by ViteJS.
    Warning, this function does not generate URLs for dependant assets.

    Arguments:
        path {str} -- Path to a Vite asset.
        config {str} -- Configuration to use.

    Raises:
        RuntimeError: If cannot find the asset path in the
            manifest (only in production).

    Returns:
        str -- The URL of this asset.
    """

    assert path is not None
    assert config is not None

    return DjangoViteAssetLoader.instance().generate_vite_asset_url(
        path, config
    )


@register.simple_tag
@mark_safe
def vite_legacy_polyfills(
    config: str = "default", **kwargs: Dict[str, str]
) -> str:
    """
    Generates a <script> tag to the polyfills generated
    by '@vitejs/plugin-legacy' if used.
    This tag must be included at end of the <body> before including
    other legacy scripts.

    Arguments:
        config {str} -- Configuration to use.

    Keyword Arguments:
        **kwargs {Dict[str, str]} -- Adds new attributes to generated
            script tags.

    Raises:
        RuntimeError: If polyfills path not found inside
            the 'manifest.json' (only in production).

    Returns:
        str -- The script tag to the polyfills.
    """

    assert config is not None

    return DjangoViteAssetLoader.instance().generate_vite_legacy_polyfills(
        config, **kwargs
    )


@register.simple_tag
@mark_safe
def vite_legacy_asset(
    path: str,
    config: str = "default",
    **kwargs: Dict[str, str],
) -> str:
    """
    Generates a <script> tag for legacy assets JS/TS
    generated by '@vitejs/plugin-legacy'
    (in production only, in development do nothing).

    Arguments:
        path {str} -- Path to a Vite asset to include
            (must contains '-legacy' in its name).
        config {str} -- Configuration to use.

    Keyword Arguments:
        **kwargs {Dict[str, str]} -- Adds new attributes to generated
            script tags.

    Raises:
        RuntimeError: If cannot find the asset path in
            the manifest (only in production).

    Returns:
        str -- The script tag of this legacy asset.
    """

    assert path is not None
    assert config is not None

    return DjangoViteAssetLoader.instance().generate_vite_legacy_asset(
        path, config, **kwargs
    )
