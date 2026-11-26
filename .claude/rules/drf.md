---
paths:
  - "src/**/*.py"
---

# Django and DRF conventions

Applies to all code under `src/`. The how-to, with a worked example, is in the `drf-endpoint`
skill (`.claude/skills/drf-endpoint/SKILL.md`).

## Layout and URLs (user decision)

- **Every app has `urls.py` (with `app_name`) and `serializers.py`.** Serializers don't live in
  `views.py`.
- **`config/urls.py` is the root URLconf and the only place that defines `api/v1/`.** It
  includes each app's `urls.py`. An app's `urls.py` never repeats `api/v1/`.
- Resource names are **singular**: `api/v1/customer/`, `api/v1/contact/`,
  `api/v1/customer/{id}/history/`. The contract is `docs/openapi.yml`.
- `/health/` is `api/v1/health/`.

## Views: DRF generic views only

- Every endpoint is a class from `rest_framework.generics`: `ListAPIView`, `CreateAPIView`,
  `RetrieveAPIView`, `ListCreateAPIView`, `RetrieveUpdateAPIView`, and so on.
  `src/utils/views.py` (`HealthView`, a `RetrieveAPIView`) is the existing example.
- No function views (`@api_view`) and no bare `APIView`. If no generic class fits, subclass
  `generics.GenericAPIView` with the mixins you need, and write why in the task ledger.
- Views stay thin: queryset, serializer, permissions, pagination. Business rules live in
  services, the most important being `apply()`.
- Restrict methods with `http_method_names`. Updates are **PATCH only**, so a save sends just
  the changed fields, and only those reach history and the outbox.

## Writes to synced records go through `apply()`

Customer and Contact are synced with Salesforce. **Never** write them with
`serializer.save()`, `Model.save()`, `.update()` or `bulk_update()`. Override `perform_create`
or `perform_update` and call `apply()`. It's the one place that runs the duplicate, ordering,
history, echo and outbox rules (`docs/design.md` §7–9). Writing past it silently breaks
history and stops the change from reaching Salesforce.

## Serializers: the trust boundary

- Explicit `fields`. Never `"__all__"`. Mark server-owned fields `read_only_fields`: IDs,
  Salesforce IDs, per-field times, sync state.
- Validate everything from outside: the webhook payload (§5) and portal edits.
- Fields outside the brief's scope are ignored. For customers: name, status, owner. For
  contacts: full name, phone, email, city, owner, customer.

## Authentication and permissions

- Set `DEFAULT_PERMISSION_CLASSES` to `IsAuthenticated`. Public views (only `api/v1/health/`) opt out
  explicitly with `AllowAny`.
- Webhook view: `authentication_classes = [TokenAuthentication]` and a permission class that
  only lets the Salesforce integration user in (Q11). `TokenAuthentication` must come first,
  so a bad token gets **401**, not 403.
- Portal API: `TokenAuthentication`, one token per portal user (assumption Q14). The actor on
  an edit is `request.user` (Q5).

## Scale: 5 million records

- Every list endpoint is paginated with `CursorPagination`, ordered by an indexed column. No
  offset pagination, and no `COUNT(*)` over big tables.
- Querysets use `select_related` / `prefetch_related`. No N+1 queries: tests check them with
  `django_assert_num_queries`.
- Filter and order only on indexed columns. Add the index in the same migration as the field.
- No unbounded queries, and no Python loops over whole tables.

## Django admin

- Register every model. Set `list_display`, `search_fields` (indexed columns only),
  `list_select_related` and `ordering`.
- Big tables: `show_full_result_count = False`, and `raw_id_fields` or `autocomplete_fields` for
  foreign keys, so a 5M-row dropdown is never rendered.
- `field_change` and `change_event` are append-only: `has_add_permission`,
  `has_change_permission` and `has_delete_permission` return `False`.
- `outbox` is read-only, except an admin action that requeues `needs_review` rows (Q9).

## Models and migrations

- Commit migrations with the model change. `uv run python src/manage.py makemigrations --check`
  must be clean.
- Uniqueness the rules depend on is enforced by the database, not Python: for example the
  unique `change_event.event_id` (§8).
- Times are timezone-aware (`USE_TZ = True`).

## Before handing off

```sh
uv run python src/manage.py check
uv run python src/manage.py makemigrations --check --dry-run
uv run pytest -q
```

Paste the output summary into the ledger.
