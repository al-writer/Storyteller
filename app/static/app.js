/* Генератор сюжетов: клиентская часть на Alpine.js. */
"use strict";

const STORAGE_KEY = "storyteller:v1";
const RECENT_LIMIT = 40;
const ATTACHED = new Set([",", "."]);
const TONES = [
  { id: "auto", title: "Авто" },
  { id: "dark", title: "Мрачный" },
  { id: "fairy", title: "Сказочный" },
  { id: "cyber", title: "Кибер" },
  { id: "city", title: "Город" },
  { id: "myth", title: "Миф" },
];

function readState() {
  try {
    return JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "{}") || {};
  } catch (error) {
    return {};
  }
}

function writeState(state) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch (error) {
    /* приватный режим или запрещённое хранилище — работаем без него */
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...options,
  });
  if (!response.ok) {
    const error = new Error(`HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (error) {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  }
}

document.addEventListener("alpine:init", () => {
  let timer = null;
  Alpine.store("toast", {
    text: "",
    show(text) {
      this.text = text;
      clearTimeout(timer);
      timer = setTimeout(() => (this.text = ""), 2200);
    },
  });
});

function toast(text) {
  window.Alpine.store("toast").show(text);
}

window.generator = function generator(presets, startSlug) {
  const saved = readState();
  return {
    presets,
    tones: TONES,
    presetId: presets.some((p) => p.id === saved.presetId) ? saved.presetId : presets[0].id,
    tone: TONES.some((t) => t.id === saved.tone) ? saved.tone : "auto",
    wild: Boolean(saved.wild),
    slots: [],
    locked: [],
    recent: Array.isArray(saved.recent) ? saved.recent.slice(-RECENT_LIMIT) : [],
    tokens: [],
    slug: "",
    url: "",
    text: "",
    prompt: {},
    active: null,
    busy: false,

    async init() {
      document.addEventListener("keydown", (event) => this.onKey(event));
      document.addEventListener("click", (event) => {
        if (!event.target.closest(".word")) this.active = null;
      });
      if (startSlug) {
        try {
          const phrase = await api(`/api/phrase/${encodeURIComponent(startSlug)}`);
          this.presetId = phrase.preset_id;
          this.apply(phrase, phrase.slots.map((s) => s.index));
          this.locked = phrase.slots.map((s) => s.index);
          this.persist();
          history.replaceState(null, "", "/");
          return;
        } catch (error) {
          toast("Эта фраза больше недоступна — соберём новую");
        }
      }
      if (saved.presetId === this.presetId && Array.isArray(saved.slots) && saved.slots.length) {
        this.slots = saved.slots;
        this.locked = Array.isArray(saved.locked) ? saved.locked : [];
      }
      await this.generate();
    },

    onKey(event) {
      if (event.code !== "Space" || event.repeat) return;
      const tag = (document.activeElement && document.activeElement.tagName) || "";
      if (["BUTTON", "INPUT", "SELECT", "TEXTAREA", "A"].includes(tag)) return;
      if (document.activeElement && document.activeElement.isContentEditable) return;
      event.preventDefault();
      this.generate();
    },

    isLocked(index) {
      return this.locked.includes(index);
    },

    toggleLock(index) {
      this.locked = this.isLocked(index)
        ? this.locked.filter((i) => i !== index)
        : [...this.locked, index];
      this.persist();
    },

    choosePreset(id) {
      if (id === this.presetId) return;
      this.presetId = id;
      this.slots = [];
      this.locked = [];
      this.generate();
    },

    async generate(rerollIndex = null) {
      if (this.busy) return;
      this.busy = true;
      try {
        const phrase = await api("/api/generate", {
          method: "POST",
          body: JSON.stringify({
            preset_id: this.presetId,
            slots: this.slots.map(({ id, number, gender, superlative }) => ({ id, number, gender, superlative })),
            locked_indices: this.locked,
            reroll_index: rerollIndex,
            recent_ids: this.recent,
            tone: this.tone,
            wild: this.wild,
          }),
        });
        const keep = rerollIndex === null
          ? this.locked
          : phrase.slots.map((s) => s.index).filter((i) => i !== rerollIndex);
        this.apply(phrase, keep);
        const fresh = phrase.slots.filter((s) => !keep.includes(s.index)).map((s) => s.id);
        this.recent = [...this.recent, ...fresh].slice(-RECENT_LIMIT);
        this.persist();
      } catch (error) {
        toast(error.status === 429 ? "Не так быстро 🙂" : "Не получилось собрать фразу");
      } finally {
        this.busy = false;
      }
    },

    reroll(index) {
      this.active = null;
      this.generate(index);
    },

    apply(phrase, keep) {
      this.slots = phrase.slots;
      this.slug = phrase.slug;
      this.url = phrase.url;
      this.text = phrase.text;
      this.prompt = phrase.prompt;
      this.locked = this.locked.filter((i) => i < phrase.slots.length);
      // Незакреплённые слова «прокручиваются» с задержкой по номеру слота.
      this.tokens = phrase.tokens.map((token, i) => ({
        text: token.text,
        slot: token.slot,
        space: i > 0 && !ATTACHED.has(token.text),
        fresh: token.slot !== null && !keep.includes(token.slot),
        delay: token.slot === null ? 0 : 60 + token.slot * 70,
      }));
    },

    persist() {
      writeState({
        presetId: this.presetId,
        tone: this.tone,
        wild: this.wild,
        slots: this.slots.map(({ id, number, gender, superlative }) => ({ id, number, gender, superlative })),
        locked: this.locked,
        recent: this.recent,
      });
    },

    async copyPrompt(kind) {
      if (!this.prompt[kind]) return;
      toast((await copyText(this.prompt[kind])) ? "Промпт скопирован" : "Не удалось скопировать");
    },

    async save() {
      try {
        await api("/api/save", { method: "POST", body: JSON.stringify({ slug: this.slug }) });
        await api(`/api/like/${this.slug}`, { method: "POST" });
        toast("Фраза в зале славы ❤️");
      } catch (error) {
        toast(error.status === 429 ? "Не так быстро 🙂" : "Не удалось сохранить");
      }
    },

    async share() {
      const coarse = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
      if (coarse && navigator.share) {
        try {
          await navigator.share({ title: "Генератор сюжетов", text: this.text, url: this.url });
          return;
        } catch (error) {
          if (error && error.name === "AbortError") return;
        }
      }
      toast((await copyText(this.url)) ? "Ссылка скопирована" : "Не удалось скопировать");
    },
  };
};

window.likeButton = function likeButton(slug, likes) {
  return {
    likes,
    liked: false,
    async like() {
      if (this.liked) return;
      this.liked = true;
      this.likes += 1; // оптимистично
      try {
        const result = await api(`/api/like/${slug}`, { method: "POST" });
        this.likes = result.likes;
      } catch (error) {
        this.likes -= 1;
        this.liked = false;
        toast("Не удалось поставить лайк");
      }
    },
  };
};

window.feed = function feed() {
  return {
    sort: "top",
    items: [],
    cursor: null,
    done: false,
    loading: false,
    observer: null,

    async load() {
      if (!this.observer) {
        this.observer = new IntersectionObserver((entries) => {
          if (entries.some((e) => e.isIntersecting)) this.more();
        });
        this.observer.observe(this.$refs.sentinel);
      }
      await this.more();
    },

    async more() {
      if (this.loading || this.done) return;
      this.loading = true;
      const sort = this.sort;
      try {
        const params = new URLSearchParams({ sort, limit: "20" });
        if (this.cursor) params.set("cursor", this.cursor);
        const page = await api(`/api/feed?${params}`);
        if (sort !== this.sort) return;
        this.items.push(...page.items.map((item) => ({ ...item, liked: false })));
        this.cursor = page.next_cursor;
        this.done = !page.next_cursor;
      } catch (error) {
        toast("Лента не загрузилась");
        this.done = true;
      } finally {
        this.loading = false;
      }
    },

    switchTo(sort) {
      if (sort === this.sort) return;
      this.sort = sort;
      this.items = [];
      this.cursor = null;
      this.done = false;
      this.loading = false;
      this.more();
    },

    async like(item) {
      if (item.liked) return;
      item.liked = true;
      item.likes += 1;
      try {
        const result = await api(`/api/like/${item.slug}`, { method: "POST" });
        item.likes = result.likes;
      } catch (error) {
        item.likes -= 1;
        item.liked = false;
        toast("Не удалось поставить лайк");
      }
    },
  };
};
