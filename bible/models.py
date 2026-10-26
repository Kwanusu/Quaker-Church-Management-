from django.db import models


class BibleVersion(models.Model):
    """A translation. Shared by all churches on the platform."""
    code = models.SlugField(max_length=16, unique=True)  # e.g. 'kjv'
    name = models.CharField(max_length=120)
    language = models.CharField(max_length=10, default='en')
    license = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return self.name


class BibleBook(models.Model):
    TESTAMENTS = [('OT', 'Old Testament'), ('NT', 'New Testament')]

    version = models.ForeignKey(BibleVersion, on_delete=models.CASCADE, related_name='books')
    number = models.PositiveSmallIntegerField()  # 1..66, canonical order
    name = models.CharField(max_length=40)
    slug = models.SlugField(max_length=40)
    testament = models.CharField(max_length=2, choices=TESTAMENTS)
    chapter_count = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ['number']
        constraints = [
            models.UniqueConstraint(fields=['version', 'number'], name='unique_book_number_per_version'),
            models.UniqueConstraint(fields=['version', 'slug'], name='unique_book_slug_per_version'),
        ]

    def __str__(self):
        return f'{self.name} ({self.version.code})'


class Verse(models.Model):
    book = models.ForeignKey(BibleBook, on_delete=models.CASCADE, related_name='verses')
    chapter = models.PositiveSmallIntegerField()
    verse = models.PositiveSmallIntegerField()
    text = models.TextField()

    class Meta:
        ordering = ['book__number', 'chapter', 'verse']
        constraints = [
            models.UniqueConstraint(fields=['book', 'chapter', 'verse'], name='unique_verse'),
        ]

    def __str__(self):
        return f'{self.book.name} {self.chapter}:{self.verse}'
