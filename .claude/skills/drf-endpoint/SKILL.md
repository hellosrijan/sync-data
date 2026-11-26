---
name: drf-endpoint
description: How to build a DRF endpoint in this project with generic views, serializers, URLs, admin and tests, including routing writes to synced records through apply(). Use when creating or changing any API endpoint under src/.
---

# Building a DRF endpoint

The rules are in `.claude/rules/drf.md`. This is the procedure, with a worked example.

## Steps

1. **Read the requirement:** the task ledger's acceptance criteria, then the matching
   `docs/design.md` section (§5 for inbound, §6 for outbound, §7–9 for the rules).
2. **Pick the generic view** from the verbs:

   | Verbs | Class |
   |---|---|
   | GET list | `generics.ListAPIView` |
   | GET list + POST | `generics.ListCreateAPIView` |
   | GET one | `generics.RetrieveAPIView` |
   | GET one + PATCH | `generics.RetrieveUpdateAPIView` with `http_method_names = ["get", "patch"]` |
   | POST only (e.g. webhook) | `generics.CreateAPIView` |

3. **Serializer:** explicit `fields`, `read_only_fields`, and validation of every external
   input.
4. **Queryset:** `select_related` for foreign keys, ordering on an indexed column.
5. **Writes to synced records:** override `perform_create` / `perform_update` to call `apply()`.
   Never `serializer.save()`.
6. **Pagination:** `CursorPagination` on lists.
7. **Permissions:** `IsAuthenticated` by default. Use the integration-user permission for the
   webhook.
8. **URL:** add it under `src/<app>/urls.py`, included from `src/config/urls.py` under `api/`.
9. **Admin:** register any new model (rules in `drf.md`).
10. **Check:** `manage.py check`, `makemigrations --check --dry-run`, `pytest -q`.

## Worked example

This shows the shape. It's illustrative only: `apply()` and the models don't exist yet, so
the names are placeholders until they're built.

```python
# src/sync/pagination.py
from rest_framework.pagination import CursorPagination

class RecordCursorPagination(CursorPagination):
    page_size = 100
    ordering = "id"          # indexed; no COUNT(*) on 5M rows
```

```python
# src/sync/serializers.py
from rest_framework import serializers
from .models import Contact

class ContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contact
        fields = ["id", "salesforce_id", "full_name", "phone", "email", "city", "owner", "customer"]
        read_only_fields = ["id", "salesforce_id"]
```

```python
# src/sync/views.py
from rest_framework import generics
from .models import Contact
from .pagination import RecordCursorPagination
from .serializers import ContactSerializer
from .services import apply, Change   # apply() from docs/design.md §7–9; Change: build plan 2.1.1

class ContactListView(generics.ListAPIView):
    queryset = Contact.objects.select_related("customer").order_by("id")
    serializer_class = ContactSerializer
    pagination_class = RecordCursorPagination

class ContactDetailView(generics.RetrieveUpdateAPIView):
    queryset = Contact.objects.select_related("customer")
    serializer_class = ContactSerializer
    http_method_names = ["get", "patch"]      # partial edits only

    def perform_update(self, serializer):
        # Never serializer.save(): history, ordering and the outbox live in apply().
        apply(Change.from_portal(
            record=serializer.instance,
            fields=serializer.validated_data,   # only the fields the user sent
            actor=self.request.user,            # Q5: the logged-in user
        ))
```

```python
# src/sync/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path("contacts/", views.ContactListView.as_view(), name="contact-list"),
    path("contacts/<int:pk>/", views.ContactDetailView.as_view(), name="contact-detail"),
]
```

## Tests Ross expects for every endpoint

- **Happy path:** status code and body shape.
- **Validation:** bad input gets 400, and nothing is stored.
- **Auth:** no credentials gets 401; the wrong user gets 403.
- **Lists:** paginated (a cursor `next` link), with a fixed number of queries via
  `django_assert_num_queries`.
- **Synced writes:** a history row exists, an outbox row exists for portal edits, and none
  exists for webhook writes.
- **Admin:** the changelist and change pages return 200 for a superuser.
