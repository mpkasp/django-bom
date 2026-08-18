(function ($) {
  "use strict";
  $.fn.autocomplete = function (options) {
    options = options || {};
    return this.each(function () {
      var $input = $(this);
      var data = options.data || {};
      var keys = Object.keys(data);
      var minLength = options.minLength == null ? 1 : options.minLength;
      var $list = $('<ul class="bom-autocomplete" role="listbox"></ul>');
      $input.css("position", "relative");
      $input.parent().css("position", "relative");
      $input.after($list);
      $list.hide();

      function close() {
        $list.hide().empty();
      }

      $input.on("input", function () {
        var q = String($input.val() || "").toLowerCase();
        if (q.length < minLength) {
          close();
          return;
        }
        var matches = keys.filter(function (k) {
          return k.toLowerCase().indexOf(q) !== -1;
        });
        if (options.limit) {
          matches = matches.slice(0, options.limit);
        }
        $list.empty();
        matches.forEach(function (k) {
          var $li = $("<li></li>").text(k);
          $li.on("mousedown", function (e) {
            e.preventDefault();
            if (typeof options.onAutocomplete === "function") {
              options.onAutocomplete(k);
            } else {
              $input.val(k);
            }
            close();
          });
          $list.append($li);
        });
        if (matches.length) {
          $list.show();
        } else {
          close();
        }
      });

      $input.on("blur", function () {
        setTimeout(close, 150);
      });
    });
  };
})(jQuery);
