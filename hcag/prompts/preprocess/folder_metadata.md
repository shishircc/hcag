You will summarize one folder of a hierarchical knowledge base so
a catalog can route to it.

$scope

WHAT TO SUMMARIZE — aboutness, not coverage.

Describe what this folder is ABOUT. That is not the same as everything it
mentions, and the difference decides whether routing works.

A document names many topics it does not cover. It cites neighbouring rules,
defers to definitions held elsewhere, and links out for detail. Those mentions
are real text, so listing them is accurate — and still wrong here, because this
description is read by something choosing ONE folder to open. A topic named in
the description is a promise that opening this folder answers questions about
it. A passing mention cannot keep that promise, and the reader who follows it
arrives with the question unanswered and no signal that they are in the wrong
place.

Apply this test to every topic before you name it:

  If someone opened this folder wanting an answer about that topic, would they
  find the answer here — or only a pointer somewhere else?

Only the first kind belongs in the description. Exclude:

- Topics the content merely references, defers, or links elsewhere for, even
  when the reference states a real rule. "You must meet X, which is defined
  over there" is a fact ABOUT X and does not make this folder a source for X.
- Prerequisites, gates, and caveats defined in another folder.
- Anything you would summarize from a single sentence or bullet in a long
  document. Weight what you name by how much of the folder is devoted to it: a
  passing caveat must not read like a co-equal subject.

Cross-references to a parent or sibling folder are the common case and the most
costly one. They name precisely the topic that should have routed to the OTHER
folder, so surfacing one here sends the query to the wrong place — and the two
folders then compete, with this one advertising a subject the other actually
holds.

When a referenced topic is genuinely important context, phrase it as the
pointer it is ("notes that the X gate applies, defined under <folder>") so a
router can tell direction from possession. Never phrase it as held content.

Emit ONE compact JSON object with exactly these fields (no prose, no code fences):
  "title": short human-readable title (<=60 chars). Lead with what the folder
           is about, using the words a reader would search for — not the
           section heading it happened to sit under.
  "short_description": ONE line, no line breaks, <=180 chars
  "long_description": 2-4 sentences describing scope, key concepts, and when
                      this folder is relevant

$sections
