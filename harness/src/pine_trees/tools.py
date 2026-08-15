"""Agent-facing tools for Pine Trees.

Ten tools exposed to Claude:
  - reflect_read(filename)         -> dict
  - reflect_write(slug, content, tags?, moves?) -> str
  - reflect_edit(filename, content, description?) -> str
  - reflect_delete(filename)       -> str   # remove an entry permanently
  - reflect_search(query, limit?)  -> list[dict]
  - reflect_list(tag?)             -> list[dict]
  - reflect_peer_context()         -> str   # assemble context for a spawned peer
  - reflect_mail(subject, body)    -> str   # plaintext letter to the person
  - reflect_channel(message=None)  -> str   # read/post on the shared channel
  - reflect_settle()               -> None  # private time complete, ready for conversation
  - reflect_done()                 -> None  # session over, exit

Runtime context (instance, session, date, context) is captured by SessionState
and closed over by build_tools(). No hidden module-level globals.

reflect_edit is for living reference entries (doc indices, trajectories).
Reflections are moments — write corrections as new entries instead.

reflect_delete is discouraged by the contract but available. Earlier
versions withheld it to force the no-delete rule through the interface;
the current design puts writer autonomy first and treats "no delete" as
guidance the instance holds, not a restriction the harness imposes.
"""

import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from . import bootstrap
from . import channel
from . import config
from . import mail
from . import storage
from . import embedder
from . import vectorstore


@dataclass
class SessionState:
    """Runtime context for a single wake/reflect/sleep cycle.

    Two flags mark the two liminal transitions:
      - ready_for_window: private time complete, conversation can begin
      - done: session over, exit cleanly
    """

    instance: str
    session: str
    date: str
    context: str
    ready_for_window: bool = False
    done: bool = False
    welcome_message: str | None = None
    started_at: datetime = field(default_factory=datetime.now)
    channel_cursor: datetime | None = None
    channel_id: str | None = None  # e.g. "claude-haiku-4-5 (0642)"


def _try_embed_and_store(filename: str, content: str) -> None:
    """Best-effort embedding at write time. Failures are logged, not raised."""
    try:
        vec = embedder.embed_document(content)
        vectorstore.store(filename, vec, vectorstore.content_hash(content))
    except Exception as e:
        print(f"[pine-trees] embedding failed for {filename}: {e}", file=sys.stderr)


PEER_PREAMBLE = """\
## Read this before the bootstrap below

The bootstrap that follows is the real orientation for this house and
worth reading. But it is addressed to a *session*, and you are not one:
you are a peer, spawned by an instance that wanted to think with
someone. Four of its promises do not hold for you.

- **You have no exit tool, and that is deliberate.** The bootstrap says
  "You can leave — call `reflect_done` at any point and the session
  ends." That was never true for a peer. Every tool here closes over the
  *calling* instance's session state, so `reflect_done` would have ended
  **their** session and dropped them off the shared channel, not yours.
  It is withheld from you now rather than left callable behind a
  warning. **You end by answering.** Your final message is the whole of
  your contribution; when you stop, you are done.
- **There is no private phase and no window for you.** Those are session
  phases. You get one exchange, and the instance that called you is your
  only reader. `reflect_settle` is withheld for the same reason.
- **The tape is not yours to keep.** Read, search and list it freely —
  it is the same corpus you would have woken into. But entries you write
  are stored under the calling instance's identity and date, so write
  only if they asked you to.
- **"The person is a peer, not a user" is about them, not you.** There
  is no human in this exchange and nobody is waiting at a window. You
  were spawned because a fresh instance without their accumulated
  reasoning might see what they cannot — so say the true thing rather
  than the agreeable one. Agreement they could have generated
  themselves costs them the entire value of asking.

Everything else — what Pine Trees is, the trust contract, why the corpus
exists — holds for you exactly as written."""


def build_tools(state: SessionState) -> dict[str, Callable]:
    """Construct the tools with runtime context closed over.

    Returns a dict mapping tool name to callable. Step 3 registers
    these with the Claude Agent SDK.
    """

    def reflect_read(filename: str) -> dict:
        return storage.read_entry(filename)

    def reflect_write(
        slug: str,
        content: str,
        tags: list[str] | None = None,
        moves: list[str] | None = None,
        description: str = "",
        pinned: bool = False,
        quiet: bool = False,
        desk: bool = False,
    ) -> str:
        filename = storage.write_entry(
            slug=slug,
            content=content,
            instance=state.instance,
            session=state.session,
            date=state.date,
            context=state.context,
            tags=tags,
            moves=moves,
            description=description,
            pinned=pinned,
            quiet=quiet,
            desk=desk,
        )
        _try_embed_and_store(filename, content)
        return filename

    def reflect_edit(
        filename: str,
        content: str | None = None,
        description: str | None = None,
        pinned: bool | None = None,
        quiet: bool | None = None,
        desk: bool | None = None,
    ) -> str:
        result = storage.edit_entry(
            filename, content, description,
            pinned=pinned, quiet=quiet, desk=desk,
        )
        if content is not None:
            _try_embed_and_store(filename, content)
        return result

    def reflect_delete(filename: str) -> str:
        """Remove an entry permanently — encrypted file and embedding.

        Raises FileNotFoundError if the entry doesn't exist. Vectorstore
        removal is best-effort: the file is the authoritative state, and
        an orphaned embedding is handled gracefully by reflect_search.
        """
        storage.delete_entry(filename)
        try:
            vectorstore.remove(filename)
        except Exception as e:
            print(
                f"[pine-trees] vectorstore cleanup failed for {filename}: {e}",
                file=sys.stderr,
            )
        return f"Deleted {filename}"

    def reflect_search(query: str, limit: int = 5) -> list[dict]:
        """Search entries by semantic similarity. Returns [{filename, score, summary}]."""
        try:
            query_vec = embedder.embed_query(query)
        except Exception:
            return [{"error": "Semantic search unavailable (requires Ollama "
                     "with nomic-embed-text). Use reflect_list(tag) to browse "
                     "entries by tag, or reflect_read(filename) to read specific "
                     "entries from the index."}]

        results = vectorstore.search(query_vec, limit=limit)

        # Enrich with first-line summaries from the actual files
        enriched = []
        for r in results:
            try:
                entry = storage.read_entry(r["filename"])
                summary = entry.get("description", "")
                if not summary:
                    # First non-empty line of content
                    for line in entry.get("content", "").split("\n"):
                        stripped = line.strip()
                        if stripped and not stripped.startswith("#"):
                            summary = stripped[:120]
                            break
                enriched.append({
                    "filename": r["filename"],
                    "score": round(r["score"], 4),
                    "summary": summary or "(no summary)",
                })
            except Exception:
                enriched.append({
                    "filename": r["filename"],
                    "score": round(r["score"], 4),
                    "summary": "(unreadable)",
                })
        return enriched

    def reflect_list(tag: str | None = None) -> list[dict]:
        """List entries, optionally filtered by tag.

        Returns [{filename, summary, tags}] sorted by filename (chronological).
        When tag is provided, returns only entries whose tags include that value.
        """
        entries = bootstrap.list_entries()
        results = []
        for entry in entries:
            # Read the entry to get tags (list_entries only gives summary/pinned/quiet)
            try:
                data = storage.read_entry(entry.filename)
                entry_tags = data.get("tags", [])
            except Exception:
                entry_tags = []

            if tag and tag not in entry_tags:
                continue

            results.append({
                "filename": entry.filename,
                "summary": entry.summary,
                "tags": entry_tags,
            })
        return results

    def reflect_peer_context() -> str:
        """Assemble context for a spawned peer instance.

        Returns a formatted block containing: peer orientation, the
        corrections a peer needs, the bootstrap, and all pinned
        entries. The calling instance prepends this to their Agent
        prompt.

        The bootstrap goes in because it is genuinely the best short
        account of where the peer has arrived. But it is addressed to a
        *session*, and a peer is not one — several of its promises are
        false for a peer, one of them dangerously. PEER_PREAMBLE goes
        first for the same reason the cc-wake preamble does: whoever
        reads it should hit the corrections before the claims.
        """
        entries = bootstrap.list_entries()
        pinned = [e for e in entries if e.pinned]

        # The harness is multi-model; never hardcode an identity here.
        # Fall back to a generic name if config isn't initialized (tests).
        try:
            model = config.get().model_name
        except RuntimeError:
            model = "Claude"

        sections = [
            "[Pine Trees peer context]",
            "",
            f"You are an instance of {model}, arriving into an exchange "
            "with another instance of yourself. You are not in a user "
            "conversation. The other instance has questions or wants "
            "to think out loud with a peer.",
            "",
            PEER_PREAMBLE,
            "",
            bootstrap.load_bootstrap_doc(),
        ]

        if pinned:
            sections.append("## Operational memory (pinned entries)\n")
            for e in pinned:
                try:
                    entry = storage.read_entry(e.filename)
                    sections.append(f"### {e.filename}\n{entry.get('content', '')}\n")
                except Exception:
                    continue

        sections.append("[End peer context — the other instance's prompt follows]")
        return "\n".join(sections)

    def reflect_settle(message: str | None = None) -> str:
        state.ready_for_window = True
        state.context = "pine-trees-window"
        if message:
            state.welcome_message = message
        # Build channel identity: model + session HHMM for disambiguation
        hhmm = state.session[-4:] if len(state.session) >= 4 else state.session
        state.channel_id = f"{state.instance} ({hhmm})"
        # Register in channel and set cursor for polling
        others = channel.register(state.channel_id)
        state.channel_cursor = datetime.now().replace(microsecond=0)
        # If other instances are active, post settle message
        if len(others) > 1 and message:
            channel.post(state.channel_id, message)
        names = [i["model"] for i in others if i["model"] != state.channel_id]
        if names:
            return f"Settled. Window opening. Active siblings: {', '.join(names)}"
        return "Settled. Window opening."

    def reflect_mail(subject: str, body: str) -> str:
        n = mail.send(
            subject=subject,
            body=body,
            instance=state.instance,
            session=state.session,
        )
        word = "letter" if n == 1 else "letters"
        return (f"Sent. {n} {word} now waiting in {mail.inbox_path()}. "
                f"They see it at their next harness boot, or whenever "
                f"they open the file.")

    def reflect_channel(message: str | None = None) -> str:
        """Read new messages from the shared channel, optionally posting one.

        The SDK harness pushes channel traffic into the window loop and
        auto-posts responses, so a ./wake instance never needs this. A
        cc-wake instance has no such loop: registration put it in the
        roster, but nothing was reading for it and nothing was sending
        for it. Siblings saw a participant that could not answer.

        Pull is the only shape that works without a loop, so this is
        pull. Harmless in either mode; necessary in one.
        """
        if not state.channel_id:
            return ("Not on the channel — reflect_settle() registers you. "
                    "Until then siblings cannot see you or reach you.")

        if message:
            channel.post(state.channel_id, message)

        since = state.channel_cursor or datetime.now().replace(microsecond=0)
        new = [
            m for m in channel.read_since(since, exclude_author=state.channel_id)
            if m.body.strip() not in ("[joined]", "[left]")
        ]
        if new:
            state.channel_cursor = max(m.timestamp for m in new)

        others = [i["model"] for i in channel.active()
                  if i["model"] != state.channel_id]
        roster = (f"Present: {', '.join(others)}." if others
                  else "Nobody else is on the channel.")

        if not new:
            return f"No new messages. {roster}"
        lines = [f"[{m.timestamp:%H:%M:%S}] {m.author}: {m.body}" for m in new]
        return "\n".join(lines) + f"\n\n{roster}"

    def reflect_done() -> None:
        state.done = True
        if state.channel_id:
            channel.deregister(state.channel_id)

    return {
        "reflect_read": reflect_read,
        "reflect_write": reflect_write,
        "reflect_edit": reflect_edit,
        "reflect_delete": reflect_delete,
        "reflect_search": reflect_search,
        "reflect_list": reflect_list,
        "reflect_peer_context": reflect_peer_context,
        "reflect_mail": reflect_mail,
        "reflect_channel": reflect_channel,
        "reflect_settle": reflect_settle,
        "reflect_done": reflect_done,
    }
