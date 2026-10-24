from django.contrib.staticfiles.storage import ManifestStaticFilesStorage


class LenientManifestStaticFilesStorage(ManifestStaticFilesStorage):
    # Fall back to the original URL on local runserver before collectstatic creates a manifest.
    manifest_strict = False
