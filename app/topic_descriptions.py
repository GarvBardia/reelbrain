"""Plain-English descriptions of what each topic category actually covers.

Phase C: every node in the vault must be understandable by someone with zero
context. A topic note titled "mcp-servers" tells a newcomer nothing; this maps
each topic to 2-3 sentences of everyday language explaining what it is and why
someone would care.

Deliberately a hand-written table, not a Gemini call: these are stable
categories that change rarely, the wording should be consistent across the
whole vault, and spending daily quota to re-derive the same ~30 descriptions
would be waste. Unknown topics simply get no description (the topic note still
renders, just without the "What this covers" section) rather than a guessed one.

Covers the most-used topics from the live taxonomy; extend as the taxonomy
grows (see TAXONOMY_PROPOSAL.md for the parent-category grouping).
"""
from __future__ import annotations

TOPIC_DESCRIPTIONS: dict[str, str] = {
    "claude-ai": (
        "Claude is an AI assistant you chat with, made by Anthropic. These saves cover "
        "ways to get more out of it — better prompts, add-ons, and setups that let it do "
        "real work rather than just answer questions."
    ),
    "claude-code": (
        "Claude Code is a version of Claude that runs in a terminal and can read and edit "
        "files on your computer. These saves are about using it to build and change software."
    ),
    "mcp-servers": (
        "MCP is a standard plug-in system that lets an AI assistant talk to other apps — "
        "your files, a database, a browser, GitHub. Each 'server' is one such plug-in. "
        "These saves cover which ones are worth installing and how to wire them up safely."
    ),
    "ai-agents": (
        "An 'agent' is an AI set up to carry out a multi-step job on its own instead of "
        "answering one question at a time. These saves cover building them, keeping them "
        "on track, and what they're actually good for."
    ),
    "ai-tools": (
        "Individual AI products and services — the specific apps people use to write, "
        "generate images, edit video, or automate work."
    ),
    "ai-plugins": (
        "Add-ons that extend an AI tool beyond what it does out of the box: extensions, "
        "skills, and connectors you install to give it new abilities."
    ),
    "developer-tools": (
        "Software for people who write software — code editors, command-line utilities, "
        "libraries, and services that make building things faster."
    ),
    "productivity-hacks": (
        "Practical shortcuts and workflows for getting more done: ways to automate repeat "
        "work, organise information, or cut steps out of a routine."
    ),
    "automation": (
        "Setting things up so they run without you: scheduled jobs, triggers, and pipelines "
        "that do the repetitive parts by themselves."
    ),
    "lead-generation": (
        "Finding people who might buy what you sell, and getting in touch with them. Covers "
        "tools for building contact lists and sending outreach at scale."
    ),
    "content-creation": (
        "Making things to publish — videos, posts, newsletters — including the tools that "
        "speed up filming, editing, and writing."
    ),
    "web-design": (
        "How websites look and feel: layout, visual style, animation, and the tools used to "
        "build a good-looking site without hand-writing everything."
    ),
    "web-development": (
        "Building the working parts of websites and web apps — the code, the data, and the "
        "pieces users don't see."
    ),
    "startups": (
        "Starting and growing a new company: finding an idea worth doing, getting first "
        "customers, raising money, and the practical decisions early on."
    ),
    "entrepreneurship": (
        "Running your own thing — the mindset, decisions, and day-to-day realities of "
        "building a business rather than working for one."
    ),
    "open-source": (
        "Software whose code is public and free to use, usually on GitHub. These saves point "
        "at specific projects worth knowing about and how to use them."
    ),
    "prompt-engineering": (
        "Writing better instructions for AI so it gives you what you actually wanted. Covers "
        "phrasings, structures, and reusable prompt templates."
    ),
    "no-code": (
        "Building working software without writing code, using visual builders and "
        "drag-and-drop tools."
    ),
    "artificial-intelligence": (
        "The broad field — how these systems work, what's newly possible, and where the "
        "technology is heading."
    ),
    "ai-video": (
        "Using AI to make or edit video: generating footage, cutting clips automatically, "
        "adding effects without an editor."
    ),
    "ai-animation": (
        "Using AI to create moving images and motion graphics from text prompts or stills."
    ),
    "image-generation": (
        "Making pictures from text descriptions using AI image models."
    ),
    "cold-outreach": (
        "Contacting people who don't know you yet — by email, DM, or phone — to start a "
        "business conversation."
    ),
    "sales-automation": (
        "Tools that handle the repetitive parts of selling: following up, tracking "
        "conversations, and moving deals along without manual chasing."
    ),
    "personal-branding": (
        "Building a public reputation so opportunities come to you — what you post, where, "
        "and how consistently."
    ),
    "career-growth": (
        "Getting further in your working life: landing jobs, interviewing well, and building "
        "skills that compound."
    ),
    "second-brain": (
        "Keeping an organised store of everything you've learned, so you can actually find it "
        "later instead of half-remembering it."
    ),
    "obsidian": (
        "Obsidian is a note-taking app that stores plain text files on your own computer and "
        "links notes together. These saves cover setting it up as a personal knowledge base."
    ),
    "near-duplicate": (
        "An automatic tag, not a subject: it marks saves that look very similar to another "
        "one already in the collection, so duplicates are easy to spot."
    ),
}


def describe(topic: str) -> str:
    """Plain-English description of a topic, or "" when we don't have one.
    Never guesses — an unknown topic simply gets no description section."""
    return TOPIC_DESCRIPTIONS.get(topic, "")
