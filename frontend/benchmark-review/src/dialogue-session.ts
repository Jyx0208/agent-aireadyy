export const DIALOGUE_SESSION_STORAGE_KEY = "pride-agent-dialogue-session";

export function freshDialogueSessionId(): string {
  return `discovery-dialogue:${crypto.randomUUID()}`;
}

export function storeDialogueSessionId(sessionId: string): void {
  try {
    window.sessionStorage.setItem(DIALOGUE_SESSION_STORAGE_KEY, sessionId);
  } catch {
    // Session storage is optional; the in-memory id still isolates the chat.
  }
}

export function startDialogueSessionId(): string {
  // The SDK session and the live strategy card must have the same lifecycle.
  // React state resets on a full page load, so reusing an old storage id would
  // let the model remember a strategy that the card no longer contains.
  const created = freshDialogueSessionId();
  storeDialogueSessionId(created);
  return created;
}
