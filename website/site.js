// Dubify Studio website: the before/after demo, download links and the copy button.
// Everything here is an enhancement: without JavaScript the page shows the
// finished dub and the download buttons link straight to the latest release.

const REPO = "MorkMongkul/dubbify-studio";
const ASSETS = {
  arm: { name: "Dubify-Studio-Apple-Silicon.dmg", pattern: /(aarch64|arm64|apple-silicon)\.dmg$/i },
  intel: { name: "Dubify-Studio-Intel.dmg", pattern: /(x64|x86_64|intel)\.dmg$/i },
};
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

setUpDemo();
setUpDownloads();
setUpCopyButtons();

function setUpDemo() {
  const demo = document.querySelector("[data-demo]");
  if (!demo) return;
  const buttons = demo.querySelectorAll("[data-show]");
  let touched = false;

  const show = (state) => {
    demo.dataset.state = state;
    buttons.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.show === state)));
  };
  buttons.forEach((b) =>
    b.addEventListener("click", () => {
      touched = true;
      show(b.dataset.show);
    }),
  );

  if (reduceMotion || !("IntersectionObserver" in window)) {
    show("dub");
    return;
  }
  // Play the dub once, when the subtitle line itself is on screen (not just
  // the top of the frame) and its fonts are in.
  const observer = new IntersectionObserver(
    (entries) => {
      if (!entries.some((e) => e.isIntersecting)) return;
      observer.disconnect();
      document.fonts.ready.then(() =>
        setTimeout(() => {
          if (!touched) show("dub");
        }, 900),
      );
    },
    { threshold: 0.6, rootMargin: "0px 0px -10% 0px" },
  );
  observer.observe(demo.querySelector(".hardsub"));
}

async function setUpDownloads() {
  const section = document.querySelector("[data-downloads]");
  if (!section) return;

  const mac = await detectMac();
  if (!mac.isMac) section.querySelector("[data-not-mac]").hidden = false;
  if (mac.arch) markThisMac(section, mac.arch);

  let release;
  try {
    const response = await fetch(`https://api.github.com/repos/${REPO}/releases/latest`, {
      headers: { Accept: "application/vnd.github+json" },
    });
    if (response.status === 404) {
      showNoRelease(section);
      return;
    }
    if (!response.ok) return; // e.g. rate-limited: the direct links still work
    release = await response.json();
  } catch {
    return; // offline: the direct links still work
  }

  for (const [arch, { name, pattern }] of Object.entries(ASSETS)) {
    const asset =
      release.assets.find((a) => a.name === name) || release.assets.find((a) => pattern.test(a.name));
    if (!asset) continue;
    section.querySelector(`[data-asset="${arch}"]`).href = asset.browser_download_url;
    section.querySelector(`[data-size="${arch}"]`).textContent = `${Math.round(asset.size / 1e6)} MB`;
  }

  const version = release.tag_name.replace(/^v/, "");
  const date = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric" }).format(
    new Date(release.published_at),
  );
  section.querySelector("[data-release]").textContent = `Version ${version}, released ${date}.`;
}

function showNoRelease(section) {
  section.querySelector("[data-no-release]").hidden = false;
  section.querySelectorAll("[data-asset]").forEach((link) => {
    link.removeAttribute("href");
    link.setAttribute("aria-disabled", "true");
    link.classList.add("is-disabled");
  });
}

function markThisMac(section, arch) {
  for (const choice of section.querySelectorAll("[data-choice]")) {
    const isThisMac = choice.dataset.choice === arch;
    choice.classList.toggle("is-this-mac", isThisMac);
    choice.querySelector(".this-mac").hidden = !isThisMac;
    choice.querySelector(".btn").classList.toggle("btn-quiet", !isThisMac);
  }
}

// Only says which chip a Mac has when the browser is sure. Safari doesn't
// tell, and then both downloads stay equal.
async function detectMac() {
  const ua = navigator.userAgent;
  const isIpad = /Macintosh/.test(ua) && navigator.maxTouchPoints > 1;
  if (!/Macintosh|Mac OS X/.test(ua) || isIpad) return { isMac: false };

  try {
    const hints = await navigator.userAgentData?.getHighEntropyValues(["architecture"]);
    if (hints?.architecture === "arm") return { isMac: true, arch: "arm" };
    if (hints?.architecture === "x86") return { isMac: true, arch: "intel" };
  } catch {
    // no client hints
  }
  return { isMac: true, arch: archFromGpu() };
}

function archFromGpu() {
  try {
    const gl = document.createElement("canvas").getContext("webgl");
    if (!gl) return null;
    const info = gl.getExtension("WEBGL_debug_renderer_info");
    const renderer = String(gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : gl.RENDERER));
    if (/Apple M\d/i.test(renderer)) return "arm";
    if (/Intel|AMD|Radeon|NVIDIA/i.test(renderer)) return "intel";
  } catch {
    // no WebGL
  }
  return null;
}

function setUpCopyButtons() {
  const status = document.querySelector("[data-copy-status]");
  for (const button of document.querySelectorAll("[data-copy]")) {
    if (!navigator.clipboard) continue; // the command stays selectable
    const source = document.getElementById(button.dataset.copy);
    button.hidden = false;
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(source.textContent.trim());
        button.textContent = "Copied";
        if (status) status.textContent = "Command copied.";
      } catch {
        window.getSelection().selectAllChildren(source);
        button.textContent = "Press ⌘C";
      }
      setTimeout(() => {
        button.textContent = "Copy";
        if (status) status.textContent = "";
      }, 2500);
    });
  }
}
