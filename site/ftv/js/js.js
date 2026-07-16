$(document).ready(function(){
	var mSswiper = new Swiper('.topbanner.mySwiper', {
		autoplay: false,
		loop:true,
		speed:800,
		spaceBetween: 1,
		slidesPerView: 2,
		navigation: {
			nextEl: '.topbanner .swiper-button-next',
			prevEl: '.topbanner .swiper-button-prev',
		},
		breakpoints: { 
			0:{
				slidesPerView: 2,
			},
			1660: {
				slidesPerView: 3,
			},
			1920: {
				slidesPerView: 4,
			},
		}
	});
	var mSswiper = new Swiper('.bt1s.mySwiper', {
		autoplay: false,
		loop:true,
		speed:800,
		spaceBetween: 1,
		slidesPerView: 1,
		autoHeight: true, 
		pagination: {
			el: '.bt1s .swiper-pagination',
			clickable: true,
			renderBullet: function (index, className) {
				return '<span class="' + className + '">' + (index + 1) + '</span>';
			},
		},
	});
	var mSswiper = new Swiper('.bt2s.mySwiper', {
		autoplay: false,
		loop:true,
		speed:800,
		spaceBetween: 1,
		slidesPerView: 1,
		autoHeight: true, 
		pagination: {
			el: '.bt2s .swiper-pagination',
			clickable: true,
			renderBullet: function (index, className) {
				return '<span class="' + className + '">' + (index + 1) + '</span>';
			},
		},
	});

	var vSswiper = new Swiper('.vedios.mySwiper', {
		autoplay: false,
	  slidesPerView: 1.5,
	  spaceBetween: 0,
	  centeredSlides: true,//센터모드
	  effect: "coverflow",
      grabCursor: true,
	  coverflowEffect: {
        rotate: 0,
        stretch: 0,
        depth: 500,
        modifier: 1,
        slideShadows: true,
      },
	navigation: {
			nextEl: '.vedios .swiper-button-next',
			prevEl: '.vedios .swiper-button-prev',
		},
	breakpoints: { 
			0:{
				slidesPerView: 1.5,
			},
			1460: {
				slidesPerView: 2.5,
			},
		},
	  loopAdditionalSlides : 1,
	  loop : true,
	  //autoplay : true,
	  speed:800,
	});
	$(".main #gnb h2 a").on("click", function(e){
		e.stopPropagation();
		if($(this).hasClass("on")){
			$("#gnb").removeClass("active");
			$("#contents").removeClass("active");
			$(this).removeClass("on");
		}else{
			$("#gnb").addClass("active");
			$("#contents").addClass("active");
			$(this).addClass("on");
		}
	});
	$(".view #gnb h2 a").on("click", function(e){
		e.stopPropagation();
		if($(this).hasClass("on")){
			$("#gnb").removeClass("active");
			$("#contents").removeClass("active2");
			$(this).removeClass("on");
		}else{
			$("#gnb").addClass("active");
			$("#contents").addClass("active2");
			$(this).addClass("on");
		}
	});
	$(".talk-tite .talk-btn").on("click", function(e){
		e.stopPropagation();
		if($(this).hasClass("on")){
			$(".talk-container").removeClass("active3");
			$("#contents").removeClass("active3");
			$(this).removeClass("on");
		}else{
			$(".talk-container").addClass("active3");
			$("#contents").addClass("active3");
			$(this).addClass("on");
		}
	});
	$(".chat-a").on("click", function(e){
		e.stopPropagation();
		$(".talk-container").removeClass("active3");
		$("#contents").removeClass("active3");
		$(".talk-tite .talk-btn").removeClass("on");
	});
	$(".h-select > a").on("click", function(e){
		e.stopPropagation();
		if($(this).hasClass("on")){
			$(this).next("div").hide();
			$(this).removeClass("on");
		}else{
			$(this).next("div").show();
			$(this).addClass("on");
		}
	});
	$("button.btn-close").on("click", function(e){
		$("#main-popup").hide();
		$("body").removeClass("hide");
	});
	$(".gnbf1-btn a").on("click", function(e){
		if($(this).hasClass("on")){
			$(".gnb-floor1 li").hide();
			$(this).find("span").text("더보기");
			$(this).removeClass("on");
		}else{
			$(".gnb-floor1 li").show();
			$(this).find("span").text("접기");
			$(this).addClass("on");
		}
	});
	var mPopswiper = new Swiper(".mPop.mySwiper", {
		autoplay: {
			delay: 3000,
			disableOnInteraction:false,
		},
		loop:true,
		speed:800,
		navigation: {
			nextEl: '.mPop .swiper-button-next',
			prevEl: '.mPop .swiper-button-prev',
		},
		pagination: {
			el: '.mPop.mySwiper .swiper-pagination',
			type: "fraction",
		},
	});
	$(".join-input-arge .all label").on("click", function(e){
		var aa=$(this).parents(".ui.checkbox").children("input").prop('checked');
		if(aa){
			//alert(11)  tr
			$(".join-more input").prop('checked',"");
		}else{
			//alert(22) fa
			$(".join-more input").prop('checked',"checked");
		}
	});
	$(".join-more input").on("change", function(e){
		//alert(123123)
		var bb=$(this).prop('checked');
		var dd=0;
		$(".join-more > div").each(function(){
			var cc=$(this).find("input").prop('checked');
			if(cc){
				dd++;
				//console.log(dd);
			}else{
				
			}
			if(dd == $(".join-more > div").length){
				$(".join-input-arge .all input").prop('checked',"checked");
			}else{
				$(".join-input-arge .all input").prop('checked',"");
			}
		});
	});
	$(".join-tab a").on("click", function(e){
		var aa=$(this).attr("class");
		var aa=aa.split(" ")[0];
		//alert(aa)
		$(".join-tab a").removeClass("on");
		$(".join-tab-con").hide();
		$("." + aa + "-con").show();
		$(this).addClass("on");
	});
	$(".join-input li.sex input").on("change", function(e){
		if($(this).prop('checked')){
			$(".join-input li.sex label").removeClass("on");
			$(this).parent("span").children("label").addClass("on");
		}else{
			//alert(22)
		}
	});
	$(".file-popup a.file-close").on("click", function(e){
		$(".file-popup").css({"display":"none"});
		$("body").css({"overflow-y":"auto"});
	});
	$(".my-tab-con td .file").on("click", function(e){
		$(".file-popup").css({"display":"flex"});
		$("body").css({"overflow-y":"hidden"});
	});
	$(".my-tab a").on("click", function(e){
		var aa=$(this).attr("class");
		var aa=aa.split(" ")[0];
		$(".my-tab a").removeClass("on");
		$(".my-tab-con").hide();
		$("."+ aa + "-con").show();
		$(this).addClass("on");
	});
	$(".my2-tab a").on("click", function(e){
		var aa=$(this).attr("class");
		var aa=aa.split(" ")[0];
		$(".my2-tab a").removeClass("on");
		$(".my2-tab-con").hide();
		$("."+ aa + "-con").show();
		$(this).addClass("on");
	});
	$(".board-tab a").on("click", function(e){
		var aa=$(this).attr("class");
		var aa=aa.split(" ")[0];
		$(".board-tab a").removeClass("on");
		$(".board-tab-con").hide();
		$("."+ aa + "-con").show();
		$(this).addClass("on");
	});
	$(".board-tab5-con .bt-tab a").on("click", function(e){
		var aa=$(this).attr("class");
		var aa=aa.split(" ")[0];
		$(".board-tab5-con .bt-tab a").removeClass("on");
		$(".board-tab5-con .bt-con").hide();
		$(".board-tab5-con ."+ aa + "-con").show();
		$(this).addClass("on");
	});
	$(".board-tab1-con .bt-tab a").on("click", function(e){
		var aa=$(this).attr("class");
		var aa=aa.split(" ")[0];
		$(".board-tab1-con .bt-tab a").removeClass("on");
		$(".board-tab1-con .bt-con").hide();
		$(".board-tab1-con ."+ aa + "-con").show();
		$(this).addClass("on");
	});
	$(".ul-slide > li > a").on("click", function(e){
		if($(this).hasClass("on")){
			$(this).next(".ul-slide-con").slideUp("fast");
			$(this).removeClass("on");
		}else{
			$(this).next(".ul-slide-con").slideDown("fast");
			$(this).addClass("on");
		}
	});
	$(".close-icon").on("click", function(e){
		$(".flex-popup").hide();
		$(".export-popup").hide();
	});
	$(".flex-con li a").on("click", function(e){
		$(".flex-popup").css({display:"flex"});
	});
	$(".flex-popup-con section button.flex").on("click", function(e){
		$(".flex-popup-con section button.flex").removeClass("on");
		$(this).addClass("on");
	});
	$(".cursor-pointer").on("change", function(e){
		$(".flex-popup-con section").hide();
		$(this).parents(".py-3-5").find("section").css({display:"flex"});
	});
	$(".my2-con li strong img").hover(function(){
		$(this).next("span").show();
	},function(){
		$(this).next("span").hide();
	});

});

