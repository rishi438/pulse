# Private API registry

`app/pulse.db` is a SQLite database created automatically at FastAPI startup. Its `private_api_registry` table is the source of truth displayed in the admin/master role editor.

## Registering a protected API

Decorate the endpoint below the FastAPI route decorator:

```python
@app.get("/api/reports")
@private_api("reports:read", "Read financial reports")
def reports(_: Annotated[dict, Depends(require_permission("reports:read"))]):
    return {"reports": []}
```

On the next server startup, the decorator metadata is inserted or updated in SQLite. The Selectize control in the management dashboard then makes `reports:read` searchable and assignable to roles.

The decorator catalogs the API; `require_permission(...)` performs the actual authorization. Keep both on every private endpoint.
