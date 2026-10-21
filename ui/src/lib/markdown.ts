import DOMPurify from 'dompurify';
import { Marked } from 'marked';

// Model output is untrusted: a file or page the agent read can steer what it writes.
// Raw HTML is shown as text, links open in the system browser (via data-external),
// and remote images wait for a click so an injected image URL cannot leak data on its own.

const marked = new Marked({
	gfm: true,
	renderer: {
		html({ text }) {
			return escapeHtml(text);
		},
		link({ href, tokens }) {
			const label = this.parser.parseInline(tokens);
			if (!isWebUrl(href)) return label;
			return `<a href="#" data-external="${escapeHtml(href)}" title="${escapeHtml(href)}">${label}</a>`;
		},
		image({ href, text }) {
			if (isWebUrl(href)) {
				const host = new URL(href).hostname;
				return `<button type="button" class="remote-image" data-remote-src="${escapeHtml(href)}">Load image from ${escapeHtml(host)}</button>`;
			}
			if (isProjectPath(href)) {
				return `<img data-project-src="${escapeHtml(href)}" alt="${escapeHtml(text)}">`;
			}
			return escapeHtml(text);
		}
	}
});

export function renderMarkdown(text: string): string {
	const html = marked.parse(text, { async: false });
	return DOMPurify.sanitize(html);
}

function isWebUrl(href: string): boolean {
	try {
		const url = new URL(href);
		return (url.protocol === 'http:' || url.protocol === 'https:') && url.hostname !== '';
	} catch {
		return false;
	}
}

function isProjectPath(href: string): boolean {
	return href !== '' && !/^[a-z][a-z0-9+.-]*:/i.test(href) && !href.startsWith('/') && !href.startsWith('\\');
}

function escapeHtml(value: string): string {
	return value
		.replaceAll('&', '&amp;')
		.replaceAll('<', '&lt;')
		.replaceAll('>', '&gt;')
		.replaceAll('"', '&quot;')
		.replaceAll("'", '&#39;');
}
