from rest_framework.pagination import CursorPagination


class RecordCursorPagination(CursorPagination):
    page_size = 50
    ordering = "id"  # Indexed B-tree lookup for stable 5M-row navigation
    cursor_query_param = "cursor"


class HistoryCursorPagination(CursorPagination):
    page_size = 50
    ordering = "-occurred_at"
    cursor_query_param = "cursor"
