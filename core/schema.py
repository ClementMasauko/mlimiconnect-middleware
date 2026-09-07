def only_versioned_api(endpoints):
    """Keep the public contract canonical while legacy /api routes remain compatible."""
    return [endpoint for endpoint in endpoints if endpoint[0].startswith("/api/v1/")]
