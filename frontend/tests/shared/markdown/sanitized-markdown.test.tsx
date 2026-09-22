import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { MARKDOWN_SANITIZE_SCHEMA } from '@/shared/markdown/sanitize-schema';
import { SanitizedMarkdown } from '@/shared/markdown/sanitized-markdown';

const renderMarkdown = (content: string) => render(<SanitizedMarkdown content={content} />);

describe('SanitizedMarkdown', () => {
  it('keeps the shared allow-list stable', () => {
    expect(MARKDOWN_SANITIZE_SCHEMA).toMatchSnapshot();
  });

  it('strips executable and disallowed HTML elements', () => {
    const { container, queryByText } = renderMarkdown(
      '<script>alert(1)</script><iframe src="https://evil.example">frame</iframe><p>safe</p>',
    );

    expect(container.querySelector('script, iframe')).toBeNull();
    expect(queryByText('alert(1)')).toBeNull();
    expect(queryByText('frame')).toBeNull();
    expect(queryByText('safe')).toBeTruthy();
  });

  it('removes event handlers and style injection from malformed nested markup', () => {
    const { container } = renderMarkdown(
      '<div style="color:red" onclick="alert(1)"><strong>safe <em>nested</strong></em></div>',
    );

    const renderedDiv = container.querySelector('div > div');
    expect(renderedDiv).toBeTruthy();
    expect(renderedDiv?.hasAttribute('style')).toBe(false);
    expect(renderedDiv?.hasAttribute('onclick')).toBe(false);
    expect(container.textContent).toContain('safe nested');
  });

  it('removes unsafe link and image schemes', () => {
    const { container } = renderMarkdown(
      '[link](javascript:alert(1)) ![image](data:text/html;base64,PHNjcmlwdD4=)',
    );

    expect(container.querySelector('a')?.hasAttribute('href')).toBe(false);
    expect(container.querySelector('img')?.hasAttribute('src')).toBe(false);
  });

  it('allows data:image URLs but no other data URLs', () => {
    const { container } = renderMarkdown('![pixel](data:image/png;base64,iVBORw0KGgo=)');

    expect(container.querySelector('img')?.getAttribute('src')).toBe(
      'data:image/png;base64,iVBORw0KGgo=',
    );
  });

  it('removes SVG and MathML attack surfaces', () => {
    const { container } = renderMarkdown(
      '<svg onload="alert(1)"><a href="javascript:alert(1)">x</a></svg><math><mi>x</mi></math>',
    );

    expect(container.querySelector('svg, math')).toBeNull();
    expect(container.querySelector('[onload]')).toBeNull();
  });

  it('forces safe links to open in a separate protected tab', () => {
    const { getByRole } = renderMarkdown('[documentation](https://example.com/docs)');

    const link = getByRole('link', { name: 'documentation' });
    expect(link.getAttribute('href')).toBe('https://example.com/docs');
    expect(link.getAttribute('target')).toBe('_blank');
    expect(link.getAttribute('rel')).toBe('noopener noreferrer');
  });

  it('covers every urlTransform branch', () => {
    const { getByText } = renderMarkdown(
      '[anchor](#section) [relative](/docs/page) [proto-relative](//cdn.example.com/x) [malformed](http://)',
    );

    expect(getByText('anchor').getAttribute('href')).toBe('#section');
    expect(getByText('relative').getAttribute('href')).toBe('/docs/page');
    expect(getByText('proto-relative').hasAttribute('href')).toBe(false);
    expect(getByText('malformed').hasAttribute('href')).toBe(false);
  });
});