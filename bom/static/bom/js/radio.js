const costInput = document.getElementById("id_unit_cost");
const costLabel =
  document.querySelector('label[for="id_unit_cost"]') ||
  (costInput && costInput.nextElementSibling);
const sellerName = document.getElementById("id_name");
const noBomEl = document.getElementById("labelNoBom");
const wBomEl = document.getElementById("labelWithBom");
const currencyEl = document.getElementById("currencyText");
const organizationEl = document.getElementById("organizationName");

if (costLabel && sellerName && noBomEl && wBomEl && currencyEl && organizationEl) {
  const noBomTxt = noBomEl.innerText;
  const wBomTxt = wBomEl.innerText;
  const currencyTxt = currencyEl.innerText;
  const organizationTxt = organizationEl.innerText;

  function handleRadioClick() {
    const currentElement = document.activeElement;
    if (document.getElementById("id_material_2").checked) {
      costLabel.innerText = noBomTxt + " | " + currencyTxt;
      sellerName.focus();
      sellerName.value = "";
      currentElement.focus();
    } else {
      costLabel.innerText = wBomTxt + " | " + currencyTxt;
      sellerName.focus();
      sellerName.value = organizationTxt;
      currentElement.focus();
    }
  }

  document.querySelectorAll('input[name="material"]').forEach((radio) => {
    radio.addEventListener("click", handleRadioClick);
  });
}
