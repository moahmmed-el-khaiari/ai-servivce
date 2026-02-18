from pydantic import BaseModel
from typing import List

class ProductItem(BaseModel):
    productId: int
    quantity: int


class MenuItem(BaseModel):
    menuId: int
    quantity: int


class CreateOrderRequest(BaseModel):
    products: List[ProductItem] = []
    menus: List[MenuItem] = []
