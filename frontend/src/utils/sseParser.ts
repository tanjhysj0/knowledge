/**
 * Parse Server-Sent Events (SSE) streams.
 *
 * The wire format is a sequence of records separated by blank lines. Each
 * record has `event:` and `data:` lines. The server may stream data byte by
 * byte, so callers feed chunks via `feed` and receive zero or more parsed
 * events per call. A trailing event with no blank line after it is flushed
 * when the consumer signals end-of-stream via `end()`.
 */

export interface SSEEvent {
  event: string;
  data: unknown;
}

export class SSEParser {
  private buffer = '';

  /**
   * Feed raw bytes (or text) and return any complete events parsed.
   * Incomplete events at the tail are buffered for the next chunk.
   */
  feed(chunk: string): SSEEvent[] {
    this.buffer += chunk;
    const events: SSEEvent[] = [];

    // SSE spec uses CRLF line endings; tolerate LF-only servers by normalizing
    // to LF so the boundary delimiter is always \n\n. We also normalize the
    // buffer itself so boundary positions and lengths match the text we slice.
    while (true) {
      const normalized = this.buffer.replace(/\r\n/g, '\n');
      const boundary = normalized.indexOf('\n\n');
      if (boundary === -1) break;
      const record = normalized.slice(0, boundary);
      // Slice the ORIGINAL (possibly CRLF) buffer at the position corresponding
      // to the end of the boundary in normalized. We can't reuse `boundary`
      // directly because each \r\n in the original adds an extra character
      // vs. the normalized form; counting CRLFs up to `boundary + 2` gives
      // the exact offset in `this.buffer`.
      const crlfCount = (normalized.slice(0, boundary + 2).match(/\r\n/g) || []).length;
      const consumedLength = boundary + 2 + crlfCount;
      this.buffer = this.buffer.slice(consumedLength);
      const parsed = this.parseRecord(record);
      if (parsed) events.push(parsed);
    }

    return events;
  }

  /**
   * Flush any pending partial record at end-of-stream.
   */
  end(): SSEEvent[] {
    const trimmed = this.buffer.trim();
    this.buffer = '';
    if (!trimmed) return [];
    const parsed = this.parseRecord(trimmed);
    return parsed ? [parsed] : [];
  }

  private parseRecord(record: string): SSEEvent | null {
    let eventName: string | null = null;
    let dataStr = '';

    for (const rawLine of record.split('\n')) {
      if (rawLine.startsWith(':')) continue; // comment / keep-alive
      if (rawLine.startsWith('event:')) {
        eventName = rawLine.slice(6).trim();
      } else if (rawLine.startsWith('data:')) {
        dataStr += rawLine.slice(5).trim();
      }
    }

    if (eventName === null || dataStr === '') return null;
    return this.tryBuild(eventName, dataStr);
  }

  private tryBuild(eventName: string, dataStr: string): SSEEvent | null {
    try {
      return { event: eventName, data: JSON.parse(dataStr) };
    } catch {
      return null;
    }
  }
}
