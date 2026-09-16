# ADR-003: AI provider abstraction and immutable metadata versions

Status: Accepted

Business logic depends on `AIProvider`, not OpenAI-specific code. OpenAI, OpenRouter and a generic OpenAI-compatible local endpoint are supported by configuration. Each result stores model, prompt version, input fingerprint, usage/cost and immutable metadata version. This prevents accidental regeneration and enables selected re-generation.

