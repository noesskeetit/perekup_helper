from typing import Any

from pydantic import BaseModel, HttpUrl, RootModel


class Category(BaseModel):
    id: int
    name: str
    slug: str
    rootId: int
    compare: bool
    pageRootId: int | None


class Location(BaseModel):
    id: int
    name: str
    namePrepositional: str
    isCurrent: bool
    isRegion: bool


class AddressDetailed(BaseModel):
    locationName: str


class PriceDetailed(BaseModel):
    enabled: bool
    fullString: str
    hasValue: bool
    postfix: str
    string: str
    stringWithoutDiscount: str | None
    title: dict[str, str]
    titleDative: str
    value: int
    wasLowered: bool
    exponent: str


class Image(RootModel):
    root: dict[str, HttpUrl]


class Geo(BaseModel):
    geoReferences: list[Any]
    formattedAddress: str


class Contacts(BaseModel):
    phone: bool
    delivery: bool
    message: bool
    messageTitle: str
    action: str
    onModeration: bool
    hasCVPackage: bool
    hasEmployeeBalanceForCv: bool
    serviceBooking: bool


class Gallery(BaseModel):
    alt: str | None = None
    cropImagesInfo: Any | None
    extraPhoto: Any | None
    hasLeadgenOverlay: bool
    has_big_image: bool
    imageAlt: str
    imageLargeUrl: str
    imageLargeVipUrl: str
    imageUrl: str
    imageVipUrl: str
    image_large_urls: list[Any]
    image_urls: list[Any]
    images: list[Any]
    imagesCount: int
    isFirstImageHighImportance: bool
    isLazy: bool
    noPhoto: bool
    showSlider: bool
    wideSnippetUrls: list[Any]


class UserLogo(BaseModel):
    link: str | None = None
    src: HttpUrl | str | None = None
    developerId: int | None


class IvaComponent(BaseModel):
    component: str
    payload: dict[str, Any] | None = None


class IvaStep(BaseModel):
    componentData: IvaComponent
    payload: dict[str, Any] | None = None
    default: bool


class Item(BaseModel):
    id: int | dict | None = None
    categoryId: int | dict | None = None
    locationId: int | dict | None = None
    isVerifiedItem: bool | None = None
    urlPath: str | None = None
    title: str | None = None
    description: str | None = None
    category: Category | None = None
    location: Location | None = None
    addressDetailed: AddressDetailed | None = None
    sortTimeStamp: int | None = None
    turnOffDate: bool | None = None
    priceDetailed: PriceDetailed | None = None
    normalizedPrice: str | None | None = None
    priceWithoutDiscount: str | None | None = None
    discountPercent: int | None | str | None | None = None
    lastMinuteOffer: str | None | dict | None | None = None
    images: list[Image] | None = None
    imagesCount: int | None = None
    isFavorite: bool | None = None
    isNew: bool | None = None
    geo: Geo | None = None
    phoneImage: str | None | None = None
    cvViewed: bool | None = None
    isXl: bool | None = None
    hasFooter: bool | None = None
    contacts: Contacts | None = None
    gallery: Gallery | None = None
    loginLink: str | None = None
    authLink: str | None = None
    userLogo: UserLogo | None = None
    isMarketplace: bool | None = None
    iva: dict[str, list[IvaStep]] | None = None
    hasVideo: bool | None = None
    hasRealtyLayout: bool | None = None
    coords: dict[str, Any] | None = None
    groupData: Any | None | None = None
    isReMapPromotion: bool | None = None
    isReserved: bool | None = None
    type: str | None = None
    ratingExperimentGroup: str | None = None
    isRatingExperiment: bool | None = None
    closedItemsText: str | None = None
    closestAddressId: int | None = None
    isSparePartsCompatibility: bool | None = None
    sellerId: str | None = None
    isPromotion: bool = False
    total_views: int | None = None
    today_views: int | None = None
    phone: str | None = None


class ItemsResponse(BaseModel):
    items: list[Item]
