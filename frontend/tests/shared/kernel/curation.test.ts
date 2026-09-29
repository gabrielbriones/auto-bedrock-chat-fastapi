import { describe, expect, it } from '@jest/globals';

import { MAX_TAGS, validateTags } from '@/shared/kernel/curation';
import { isErr, isOk } from '@/shared/kernel/result';

describe('TagPolicy', () => {
  it('accepts unique tags within the shared policy', () => {
    const result = validateTags(['performance', 'avx_512']);

    expect(isOk(result)).toBe(true);
    if (isOk(result)) {
      expect(result.value).toEqual(['performance', 'avx_512']);
    }
  });

  it('rejects duplicate, malformed, and excessive tags', () => {
    expect(isErr(validateTags(['performance', 'performance']))).toBe(true);
    expect(isErr(validateTags(['contains spaces']))).toBe(true);
    expect(isErr(validateTags(Array.from({ length: MAX_TAGS + 1 }, (_, index) => `tag${index}`)))).toBe(
      true,
    );
  });
});