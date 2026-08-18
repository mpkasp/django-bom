(function ($) {
  "use strict";

  window.enableActionCheckboxColumn = function () {
    $(".action-checkbox-column").show();
    $(".action-checkbox-hide").show();
    $(".action-checkbox-show").hide();
  };

  window.disableActionCheckboxColumn = function () {
    $(".action-checkbox-column").hide();
    $(".action-checkbox-hide").hide();
    $(".action-checkbox-show").show();
  };

  window.clearSearchExpression = function () {
    $("#autocomplete-input").val("");
    $("#id_part_class").val("");
    var form = document.getElementById("searchForm");
    if (form) {
      form.submit();
    }
  };

  $.fn.dropdown = function () {
    return this.each(function () {
      var $trigger = $(this);
      if ($trigger.data("bomDropdownBound")) {
        return;
      }
      $trigger.data("bomDropdownBound", true);
      var target = $trigger.attr("data-target");
      var $menu = $("#" + target);
      $menu.addClass("dropdown-content");
      $trigger.on("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        $(".dropdown-content").not($menu).removeClass("show");
        $menu.toggleClass("show");
      });
      $(document).on("click.bomDropdown", function () {
        $menu.removeClass("show");
      });
    });
  };

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
