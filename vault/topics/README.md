# Topics

Curated entry points into the media catalog. A topic page is a short write-up plus an embedded, filtered view of the catalog.

To make one:

1. Create `topics/<Topic Name>.md`.
2. Write a paragraph framing the topic and linking key [[../papers/|papers]].
3. Embed a filtered Base view, e.g.:

   ```
   ![[All Media.base#All media]]
   ```

   or create a dedicated `.base` file filtering on the topic's tags (copy `All Media.base` and add a `note.tags.contains("your-tag")` filter).
