(function ($) {
  "use strict";

  var pageLoadingShowTimer = null;
  var PAGE_LOADING_DELAY_FALLBACK_MS = 300;

  function pageLoadingEl() {
    return document.getElementById("bom-page-loading");
  }

  function pageLoadingDelayMs() {
    var el = pageLoadingEl();
    if (!el) {
      return PAGE_LOADING_DELAY_FALLBACK_MS;
    }
    var raw = el.getAttribute("data-delay-ms");
    var value = parseInt(raw, 10);
    if (isNaN(value) || value < 0) {
      return PAGE_LOADING_DELAY_FALLBACK_MS;
    }
    return value;
  }

  function showPageLoading() {
    var el = pageLoadingEl();
    if (!el) {
      return;
    }
    el.classList.add("is-active");
    el.setAttribute("aria-hidden", "false");
    el.setAttribute("aria-busy", "true");
  }

  function hidePageLoading() {
    if (pageLoadingShowTimer) {
      clearTimeout(pageLoadingShowTimer);
      pageLoadingShowTimer = null;
    }
    var el = pageLoadingEl();
    if (!el) {
      return;
    }
    el.classList.remove("is-active");
    el.setAttribute("aria-hidden", "true");
    el.setAttribute("aria-busy", "false");
  }

  function schedulePageLoading() {
    if (pageLoadingShowTimer || (pageLoadingEl() && pageLoadingEl().classList.contains("is-active"))) {
      return;
    }
    var delayMs = pageLoadingDelayMs();
    if (delayMs <= 0) {
      showPageLoading();
      return;
    }
    pageLoadingShowTimer = setTimeout(function () {
      pageLoadingShowTimer = null;
      showPageLoading();
    }, delayMs);
  }

  function shouldIgnoreLinkNavigation(anchor, event) {
    if (!anchor || !anchor.getAttribute) {
      return true;
    }
    if (event.defaultPrevented) {
      return true;
    }
    if (typeof event.button === "number" && event.button !== 0) {
      return true;
    }
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return true;
    }
    if (anchor.target && anchor.target !== "_self") {
      return true;
    }
    if (anchor.hasAttribute("download")) {
      return true;
    }
    var href = anchor.getAttribute("href");
    if (!href || href.charAt(0) === "#") {
      return true;
    }
    var protocol = href.split(":", 1)[0].toLowerCase();
    if (protocol === "mailto" || protocol === "tel" || protocol === "javascript") {
      return true;
    }
    try {
      var url = new URL(href, window.location.href);
      if (url.origin !== window.location.origin) {
        return true;
      }
      if (
        url.pathname === window.location.pathname &&
        url.search === window.location.search &&
        url.hash
      ) {
        return true;
      }
    } catch (err) {
      return true;
    }
    return false;
  }

  $(document).on("click.bomPageLoading", "a[href]", function (e) {
    if (shouldIgnoreLinkNavigation(this, e)) {
      return;
    }
    schedulePageLoading();
  });

  $(document).on("submit.bomPageLoading", "form", function () {
    schedulePageLoading();
  });

  $(window).on("pageshow.bomPageLoading", function (e) {
    if (e.originalEvent && e.originalEvent.persisted) {
      hidePageLoading();
    }
  });

  window.enableActionCheckboxColumn = function () {
    $(".action-checkbox-column, .action-checkbox-hide").removeClass("hidden");
    $(".action-checkbox-show").addClass("hidden");
  };

  window.disableActionCheckboxColumn = function () {
    $(".action-checkbox-column, .action-checkbox-hide").addClass("hidden");
    $(".action-checkbox-show").removeClass("hidden");
  };

  window.clearSearchExpression = function () {
    $("#autocomplete-input").val("");
    $("#id_part_class").val("");
    var form = document.getElementById("searchForm");
    if (form) {
      form.submit();
    }
  };

  function hideAllDropdowns() {
    $(".dropdown-content")
      .removeClass("show")
      .css({
        display: "",
        top: "",
        left: "",
        right: "",
        position: "",
        zIndex: "",
      });
  }

  $.fn.dropdown = function () {
    return this.each(function () {
      var $trigger = $(this);
      if ($trigger.data("bomDropdownBound")) {
        return;
      }
      $trigger.data("bomDropdownBound", true);
      var target = $trigger.attr("data-target");
      if (!target) {
        return;
      }
      var $menu = $("#" + target);
      $menu.addClass("dropdown-content");

      function placeMenu() {
        if (!$menu.hasClass("show")) {
          return;
        }
        var rect = $trigger[0].getBoundingClientRect();
        $menu.css({ left: 0, top: 0, right: "auto" });
        var menuWidth = $menu.outerWidth() || 192;
        var menuHeight = $menu.outerHeight() || 0;
        var left = document.documentElement.dir === "rtl" ? rect.right - menuWidth : rect.left;
        left = Math.max(8, Math.min(left, window.innerWidth - menuWidth - 8));
        var top = rect.bottom + 4;
        if (top + menuHeight > window.innerHeight - 8 && rect.top - menuHeight - 4 > 8) {
          top = rect.top - menuHeight - 4;
        }
        $menu.css({
          position: "fixed",
          top: top + "px",
          left: left + "px",
          right: "auto",
          zIndex: 80,
        });
      }

      if (!$menu.data("bomDropdownMenuBound")) {
        $menu.data("bomDropdownMenuBound", true);
        $menu.on("click", function (e) {
          e.stopPropagation();
        });
      }

      $trigger.on("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        var opening = !$menu.hasClass("show");
        hideAllDropdowns();
        if (opening) {
          $menu.addClass("show");
          placeMenu();
        }
      });
      $(window).on("resize.bomDropdown scroll.bomDropdown", placeMenu);
    });
  };

  $(document).on("click.bomDropdown", function () {
    hideAllDropdowns();
  });

  $(document).on("keydown.bomDropdown", function (e) {
    if (e.key === "Escape") {
      hideAllDropdowns();
    }
  });

  $.fn.modal = function () {
    return this.each(function () {
      var $el = $(this);
      $el.find(".modal-close").on("click", function (e) {
        e.preventDefault();
        $el.removeClass("open");
      });
    });
  };

  $.fn.tooltip = function () {
    return this;
  };

  $.fn.formSelect = function () {
    return this;
  };

  $.fn.collapsible = function () {
    return this.each(function () {
      var $root = $(this);
      $root.find(".collapsible-header").on("click", function () {
        $(this).closest("li").toggleClass("active");
      });
    });
  };

  $.fn.floatingActionButton = function () {
    return this;
  };

  window.M = window.M || {};
  window.M.Tabs = {
    init: function (elem) {
      if (!elem) {
        return { select: function () {} };
      }
      var $tabs = $(elem);
      var panels = [];
      $tabs.find("a[href^='#']").each(function () {
        var id = this.getAttribute("href").slice(1);
        var $panel = $("#" + id);
        if ($panel.length) {
          panels.push($panel);
        }
      });
      function isKnownPanel(id) {
        return panels.some(function ($p) {
          return $p.attr("id") === id;
        });
      }
      function panelIdFromHash() {
        var raw = (window.location.hash || "").replace(/^#/, "");
        if (!raw) {
          return null;
        }
        var id;
        try {
          id = decodeURIComponent(raw);
        } catch (err) {
          return null;
        }
        return isKnownPanel(id) ? id : null;
      }
      function syncUrl(id) {
        if (!id || !window.history || typeof window.history.replaceState !== "function") {
          return;
        }
        var search = "";
        if (window.location.search) {
          var params = new URLSearchParams(window.location.search);
          params.delete("tab_anchor");
          search = params.toString();
          search = search ? "?" + search : "";
        }
        var next = window.location.pathname + search + "#" + id;
        var current = window.location.pathname + window.location.search + window.location.hash;
        if (current === next) {
          return;
        }
        window.history.replaceState(null, "", next);
      }
      function show(id, updateUrl) {
        if (!isKnownPanel(id)) {
          return;
        }
        panels.forEach(function ($p) {
          $p.toggle($p.attr("id") === id);
        });
        $tabs.find("a").removeClass("active");
        $tabs.find("a[href='#" + id + "']").addClass("active");
        if (updateUrl) {
          syncUrl(id);
        }
      }
      $tabs.off("click.bomTabs").on("click.bomTabs", "a[href^='#']", function (e) {
        e.preventDefault();
        show(this.getAttribute("href").slice(1), true);
      });
      $(window)
        .off("hashchange.bomTabs")
        .on("hashchange.bomTabs", function () {
          var fromHash = panelIdFromHash();
          if (fromHash) {
            show(fromHash, false);
          }
        });
      var initial = $tabs.find("a.active").attr("href");
      var hashId = panelIdFromHash();
      if (initial) {
        show(initial.slice(1), false);
      } else if (hashId) {
        show(hashId, false);
      } else if (panels[0]) {
        show(panels[0].attr("id"), false);
      }
      return {
        select: function (id) {
          show(id, true);
        },
      };
    },
  };
  window.M.Collapsible = {
    init: function (elem) {
      $(elem).collapsible();
      return {};
    },
  };

  $(document).ready(function () {
    $(".dropdown-trigger").dropdown();
    $(".modal").modal();
    $(".modal-trigger").on("click", function (e) {
      var href = $(this).attr("href") || "";
      if (href.charAt(0) === "#") {
        e.preventDefault();
        $(href).addClass("open");
      }
    });
    $(".collapsible").collapsible();

    $("#action-select-all").on("change", function () {
      var checked = $(this).is(":checked");
      $("input.checkbox-array").prop("checked", checked);
    });

    var $checkbox = $(".checkbox-array");
    var lastChecked = null;
    $checkbox.on("click", function (e) {
      if (!lastChecked) {
        lastChecked = this;
        return;
      }
      if (e.shiftKey) {
        var start = $checkbox.index(this);
        var end = $checkbox.index(lastChecked);
        $checkbox
          .slice(Math.min(start, end), Math.max(start, end) + 1)
          .prop("checked", lastChecked.checked);
      }
      lastChecked = this;
    });
  });
})(jQuery);
