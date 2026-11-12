#include "DoesNotExist.fxh"
texture2D texMissing < source = "DoesNotExist.png"; > { Width = 1; Height = 1; };

uniform float BrokenParam <
    ui_type = "slider";
    ui_min = 0.0; ui_max = 1.0;
> = 0.5;
