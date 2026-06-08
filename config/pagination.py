def paginate_queryset(qs, page: int = 1, page_size: int = 50):
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    total = qs.count()
    start = (page - 1) * page_size
    end = start + page_size
    items = list(qs[start:end])
    return {
        'items': items,
        'total': total,
        'page': page,
        'page_size': page_size,
        'pages': (total + page_size - 1) // page_size,
    }


def paginate_list(data: list, page: int = 1, page_size: int = 50):
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    total = len(data)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        'items': data[start:end],
        'total': total,
        'page': page,
        'page_size': page_size,
        'pages': (total + page_size - 1) // page_size,
    }
