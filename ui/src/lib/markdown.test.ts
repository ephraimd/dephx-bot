import { describe, expect, it } from 'vitest';
import { renderMarkdown } from './markdown';

describe('renderMarkdown', () => {
	it('renders ordinary markdown', () => {
		const html = renderMarkdown('# Title\n\n- one\n- **two**\n\n`code`');
		expect(html).toContain('<h1>Title</h1>');
		expect(html).toContain('<strong>two</strong>');
		expect(html).toContain('<code>code</code>');
	});

	it('escapes raw html instead of rendering it', () => {
		const html = renderMarkdown('<script>alert(1)</script><b>bold</b><img src=x onerror=alert(1)>');
		expect(html).not.toMatch(/<script|<b>|<img/);
		expect(html).toContain('&lt;b&gt;');
	});

	it('never links javascript: urls', () => {
		const html = renderMarkdown('[click](javascript:alert(1))');
		expect(html).not.toContain('javascript:');
		expect(html).not.toContain('<a');
	});

	it('marks web links for the system browser', () => {
		const html = renderMarkdown('[docs](https://a.dev/x)');
		expect(html).toContain('data-external="https://a.dev/x"');
		expect(html).toContain('href="#"');
	});

	it('marks bare urls too', () => {
		expect(renderMarkdown('see https://who.int/malaria')).toContain(
			'data-external="https://who.int/malaria"'
		);
	});

	it('does not auto-load remote images', () => {
		const html = renderMarkdown('![a](https://evil.example/p.png?d=secret)');
		expect(html).not.toMatch(/<img[^>]+src=/);
		expect(html).toContain('Load image from evil.example');
		expect(html).toContain('data-remote-src="https://evil.example/p.png?d=secret"');
	});

	it('routes project images through data-project-src', () => {
		const html = renderMarkdown('![chart](out/chart.png)');
		expect(html).toContain('data-project-src="out/chart.png"');
		expect(html).not.toMatch(/<img[^>]+\ssrc=/);
	});

	it('shows other images as their alt text', () => {
		const html = renderMarkdown('![secret](file:///etc/passwd) ![x](/abs.png)');
		expect(html).not.toContain('<img');
		expect(html).toContain('secret');
	});
});
