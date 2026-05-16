/**
 * broadsheet sidebar launcher card — v0.2 architecture.
 *
 * Registered as a Lovelace resource by init/register-launcher.py on
 * addon boot. The companion Lovelace dashboard contains a single panel-
 * mode card of type `custom:broadsheet-launcher-card`; rendering the
 * dashboard mounts this element, which immediately redirects the top-
 * level browsing context to broadsheet's standalone URL.
 *
 * The redirect is INTENTIONAL. broadsheet runs on a dedicated host
 * port (default 8124) bypassing HA ingress, so there's no HA chrome
 * wrapping any page. The sidebar entry in HA is just a launcher — tap
 * it, leave HA, land on broadsheet bare. Return to HA via broadsheet's
 * kebab "Open Home Assistant" entry (target=_top to /).
 *
 * Why `set hass()` instead of `connectedCallback()`: HA's
 * `ha-panel-custom` and Lovelace card-element wrappers wire the `hass`
 * property AFTER mount via Lit's `@property({attribute: false})`.
 * Redirecting in connectedCallback() can fire before HA has finished
 * routing, occasionally producing a flash of broken state. The `hass`
 * setter is guaranteed to fire post-mount.
 *
 * Mixed content: top-level navigation (window.top.location.href) is
 * NOT subject to mixed-content blocking even when HA is on HTTPS and
 * broadsheet is on plain HTTP. The user sees the address bar change
 * to http://; the only effect is losing the padlock once on broadsheet.
 *
 * @@PORT@@ is substituted by run.sh at install time from the addon's
 * `host_port_override` option (default 8124 if unset).
 */
class BroadsheetLauncherCard extends HTMLElement {
  setConfig(config) {
    // Required by the Lovelace card contract. We don't accept any
    // configuration — the launcher is purely a redirect.
  }

  set hass(hass) {
    if (this._navigated) return;
    this._navigated = true;
    const host = window.location.hostname;
    const port = @@PORT@@;
    const url = `http://${host}:${port}/`;
    // Sniff for HA Companion app webview — on mobile we want the
    // OS browser to take over (not the in-app webview), so a tap
    // delivers the user to a "real" tab they can keep open.
    const isCompanionApp = /Home Assistant/i.test(navigator.userAgent);
    if (isCompanionApp) {
      window.open(url, "_blank");
    } else {
      window.top.location.href = url;
    }
  }

  getCardSize() {
    return 1;
  }
}

customElements.define("broadsheet-launcher-card", BroadsheetLauncherCard);
