/**
 * breakpoints.js
 * Runs on page load and on resize/orientationchange.
 * Toggles bp-* classes on <html> so CSS can respond to viewport size.
 */
(() => {
  const root = document.documentElement;
  const breakpoints = {
    'bp-short-desktop': width => width >= 1100 && innerHeight <= 820,
    'bp-desktop': width => width > 1024,
    'bp-max-1024': width => width <= 1024,
    'bp-769-1024': width => width >= 769 && width <= 1024,
    'bp-max-840': width => width <= 840,
    'bp-max-768': width => width <= 768,
    'bp-321-768': width => width >= 321 && width <= 768,
    'bp-short-phone': (width, height) => width <= 380 && height <= 600,
    'bp-max-320': width => width <= 320,
    'bp-portrait': (width, height) => width >= 769 && width <= height
  };
  const update = () => {
    const { innerWidth: width, innerHeight: height } = window;
    Object.entries(breakpoints).forEach(([name, matches]) => {
      root.classList.toggle(name, matches(width, height));
    });
  };
  update();
  window.addEventListener('resize', update);
  window.addEventListener('orientationchange', update);
})();
