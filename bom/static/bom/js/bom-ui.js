(function ($) {
  "use strict";

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
      function show(id) {
        panels.forEach(function ($p) {
          $p.toggle($p.attr("id") === id);
        });
        $tabs.find("a").removeClass("active");
        $tabs.find("a[href='#" + id + "']").addClass("active");
      }
      $tabs.on("click", "a[href^='#']", function (e) {
        e.preventDefault();
        show(this.getAttribute("href").slice(1));
      });
      var initial = $tabs.find("a.active").attr("href");
      if (initial) {
        show(initial.slice(1));
      } else if (panels[0]) {
        show(panels[0].attr("id"));
      }
      return {
        select: show,
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
