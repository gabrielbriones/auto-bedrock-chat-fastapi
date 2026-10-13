import { kbDocumentId, type KbDocumentId } from '@/shared/kernel/branded';
import type { Instant } from '@/shared/kernel/instant';
import { err, ok, type Result } from '@/shared/kernel/result';

export { kbDocumentId, type KbDocumentId };

export type TagPolicyError =
  | { readonly kind: 'too-many-tags'; readonly maximum: number }
  | { readonly kind: 'duplicate-tag'; readonly tag: string }
  | { readonly kind: 'invalid-tag'; readonly tag: string };

export const MAX_TAGS = 20;
export const MAX_TAG_LENGTH = 32;

const tagPattern = /^[A-Za-z0-9_-]+$/;

export const validateTags = (
  tags: readonly string[],
): Result<readonly string[], TagPolicyError> => {
  if (tags.length > MAX_TAGS) {
    return err({ kind: 'too-many-tags', maximum: MAX_TAGS });
  }

  const seen = new Set<string>();
  for (const tag of tags) {
    if (tag.length > MAX_TAG_LENGTH || !tagPattern.test(tag)) {
      return err({ kind: 'invalid-tag', tag });
    }

    if (seen.has(tag)) {
      return err({ kind: 'duplicate-tag', tag });
    }

    seen.add(tag);
  }

  return ok([...tags]);
};

export type TagAddError =
  | { readonly kind: 'empty-tag' }
  | TagPolicyError;

// The single-tag counterpart of `validateTags`, for chip inputs. Lives beside the policy rather
// than in either consuming context, because `review` and `knowledge` share one tag vocabulary.
export const addTag = (
  tags: readonly string[],
  candidate: string,
): Result<readonly string[], TagAddError> => {
  const tag = candidate.trim();

  if (tag.length === 0) {
    return err({ kind: 'empty-tag' });
  }

  if (tag.length > MAX_TAG_LENGTH || !tagPattern.test(tag)) {
    return err({ kind: 'invalid-tag', tag });
  }

  if (tags.includes(tag)) {
    return err({ kind: 'duplicate-tag', tag });
  }

  if (tags.length >= MAX_TAGS) {
    return err({ kind: 'too-many-tags', maximum: MAX_TAGS });
  }

  return ok([...tags, tag]);
};

export type SynthesisResult = {
  readonly kbDocumentId: KbDocumentId;
  readonly synthesizedAt: Instant;
};

export type RollbackResult = {
  readonly kbDocumentId: KbDocumentId;
  readonly rolledBackAt: Instant;
};