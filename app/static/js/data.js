$(document).ready(function() {
    $('.custom-file-input').on('change', function(e) {
        $(this).closest(".custom-file").find(".custom-file-label").html(this.files[0].name);
    })
});