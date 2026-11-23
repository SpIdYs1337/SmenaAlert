"use strict";

document.addEventListener("DOMContentLoaded", () => {
  const showcase = document.querySelector("[data-tabs]");
  const list = showcase.querySelector(".tab-list");
  const tabs = [...list.querySelectorAll("button")];
  const panels = tabs.map(tab => document.getElementById(tab.getAttribute("aria-controls")));

  function select(index) {
    tabs.forEach((tab, position) => {
      const selected = position === index;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      panels[position].hidden = !selected;
    });
  }

  list.setAttribute("role", "tablist");
  tabs.forEach((tab, index) => {
    tab.setAttribute("role", "tab");
    panels[index].setAttribute("role", "tabpanel");
    panels[index].setAttribute("aria-labelledby", tab.id);
    panels[index].tabIndex = 0;
    tab.addEventListener("click", () => select(index));
  });
  list.addEventListener("keydown", event => {
    const index = tabs.indexOf(event.target);
    if (index === -1) return;
    const keys = {
      ArrowRight: index + 1, ArrowDown: index + 1, ArrowLeft: index - 1, ArrowUp: index - 1,
      Home: 0, End: tabs.length - 1,
    };
    if (!Object.hasOwn(keys, event.key)) return;
    event.preventDefault();
    const next = (keys[event.key] + tabs.length) % tabs.length;
    select(next);
    tabs[next].focus();
  });
  select(0);
  list.hidden = false;
  showcase.dataset.enhanced = "";

  if (!("IntersectionObserver" in window) || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      entry.target.classList.add("revealed");
      observer.unobserve(entry.target);
    }
  }, {threshold: 0.08});
  document.querySelectorAll("[data-reveal]").forEach(section => observer.observe(section));
});
