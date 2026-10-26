import re

from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import BibleBook, BibleVersion, Verse

# The Bible is read-only and public: allow browsers/CDNs to cache it.
CACHE_HEADERS = {'Cache-Control': 'public, max-age=86400'}


def _version(code):
    return get_object_or_404(BibleVersion, code=code.lower())


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(['GET'])
@permission_classes([AllowAny])
def versions(request):
    data = [{'code': v.code, 'name': v.name, 'language': v.language, 'license': v.license}
            for v in BibleVersion.objects.all()]
    return Response(data, headers=CACHE_HEADERS)


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(['GET'])
@permission_classes([AllowAny])
def books(request, version):
    v = _version(version)
    data = [{'number': b.number, 'name': b.name, 'slug': b.slug, 'testament': b.testament,
             'chapters': b.chapter_count} for b in v.books.all()]
    return Response({'version': v.code, 'name': v.name, 'books': data}, headers=CACHE_HEADERS)


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(['GET'])
@permission_classes([AllowAny])
def chapter(request, version, book, chapter):
    v = _version(version)
    b = get_object_or_404(BibleBook, version=v, slug=book)
    if not 1 <= chapter <= b.chapter_count:
        raise ValidationError({'chapter': f'{b.name} has {b.chapter_count} chapters.'})
    verses = list(Verse.objects.filter(book=b, chapter=chapter).values('verse', 'text'))
    neighbours = {b2.number: b2 for b2 in BibleBook.objects.filter(version=v, number__in=[b.number - 1, b.number + 1])}

    def ref(bk, ch):
        return {'book': bk.slug, 'book_name': bk.name, 'chapter': ch}

    prev = ref(b, chapter - 1) if chapter > 1 else (
        ref(neighbours[b.number - 1], neighbours[b.number - 1].chapter_count) if b.number - 1 in neighbours else None)
    nxt = ref(b, chapter + 1) if chapter < b.chapter_count else (
        ref(neighbours[b.number + 1], 1) if b.number + 1 in neighbours else None)
    return Response({
        'version': v.code, 'book': b.slug, 'book_name': b.name, 'chapter': chapter,
        'chapters': b.chapter_count, 'verses': verses, 'previous': prev, 'next': nxt,
    }, headers=CACHE_HEADERS)


REFERENCE = re.compile(r'^\s*(\d?\s?[a-z][a-z ]*?)\s+(\d+)(?::(\d+))?\s*$', re.I)


class SearchPagination(PageNumberPagination):
    page_size = 25


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(['GET'])
@permission_classes([AllowAny])
def search(request, version):
    """
    ?q=  — either a reference ("John 15:14", "Psalm 23") or words ("still waters").
    Word searches require every word to appear in the verse.
    """
    v = _version(version)
    q = request.query_params.get('q', '').strip()
    if len(q) < 2:
        raise ValidationError({'q': 'Type at least 2 characters.'})

    m = REFERENCE.match(q)
    if m:
        name = re.sub(r'\s+', ' ', m.group(1).strip().lower())
        name = {'psalm': 'psalms', 'song of songs': 'song of solomon', 'revelations': 'revelation'}.get(name, name)
        book = (BibleBook.objects.filter(version=v, name__iexact=name).first()
                or BibleBook.objects.filter(version=v, name__istartswith=name).first())
        if book:
            return Response({'type': 'reference', 'book': book.slug, 'book_name': book.name,
                             'chapter': int(m.group(2)), 'verse': int(m.group(3)) if m.group(3) else None})

    words = [w for w in re.findall(r"[\w']+", q) if len(w) > 1][:6]
    if not words:
        raise ValidationError({'q': 'Type a reference like "John 3:16" or some words.'})
    filt = Q()
    for w in words:
        filt &= Q(text__icontains=w)
    qs = Verse.objects.filter(book__version=v).filter(filt).select_related('book').order_by('book__number', 'chapter', 'verse')
    paginator = SearchPagination()
    page = paginator.paginate_queryset(qs, request)
    results = [{'book': x.book.slug, 'book_name': x.book.name, 'chapter': x.chapter, 'verse': x.verse, 'text': x.text}
               for x in page]
    return paginator.get_paginated_response(results)
