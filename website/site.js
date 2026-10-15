// Dubify Studio website: download links and the copy button.
// Everything here is an enhancement: without JavaScript both download buttons
// link straight to the latest release.

const REPO = "MorkMongkul/dubbify-studio";
const ASSETS = {
  arm: { name: "Dubify-Studio-Apple-Silicon.dmg", pattern: /(aarch64|arm64|apple-silicon)\.dmg$/i },
  intel: { name: "Dubify-Studio-Intel.dmg", pattern: /(x64|x86_64|intel)\.dmg$/i },
};

setUpDownloads();
setUpCopyButtons();

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

// The matching download becomes the bright one and is labelled "This Mac".
function markThisMac(section, arch) {
  section.classList.add("knows-mac");
  for (const link of section.querySelectorAll("[data-choice]")) {
    const isThisMac = link.dataset.choice === arch;
    link.classList.toggle("is-this-mac", isThisMac);
    link.querySelector(".dl-badge").hidden = !isThisMac;
  }
}

// Only says which chip a Mac has when the browser is sure. Safari doesn't
// tell, and then both downloads stay equal.
async function detectMac() {
  // Desktop Mac browsers say "Macintosh"; iPhones say "like Mac OS X", and
  // iPads pass as Macs but have a touch screen.
  const ua = navigator.userAgent;
  const isIpad = /Macintosh/.test(ua) && navigator.maxTouchPoints > 1;
  if (!/Macintosh/.test(ua) || isIpad) return { isMac: false };

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
